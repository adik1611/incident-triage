"""Run store, queue (memory | redis) and executor (A4). Submission returns immediately; workers execute asynchronously."""
import hashlib, json, queue, threading, time, uuid
from .config import settings
from . import metrics, guardrails, policy, agents
from .contract import AlertRequest
from .logsetup import log, corr_id

TERMINAL = {"succeeded", "escalated", "failed"}

class MemStore:
    def __init__(self): self.d, self.lock, self.q, self.cache, self.fb = {}, threading.Lock(), queue.Queue(), {}, []
    def put(self, run):
        with self.lock: self.d[run["run_id"]] = run
    def get(self, rid): return self.d.get(rid)
    def enqueue(self, rid): self.q.put(rid)
    def dequeue(self, timeout=1):
        try: return self.q.get(timeout=timeout)
        except queue.Empty: return None
    def depth(self): return self.q.qsize()
    def cache_get(self, k):
        v = self.cache.get(k)
        return v[1] if v and v[0] > time.time() else None
    def cache_put(self, k, val): self.cache[k] = (time.time() + settings.CACHE_TTL_S, val)
    def add_feedback(self, fb): self.fb.append(fb)
    def all_feedback(self): return list(self.fb)
    def recent(self, n=200): return sorted(self.d.values(), key=lambda r: -r["submitted_at"])[:n]

class RedisStore:
    def __init__(self):
        import redis; self.r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    def put(self, run): self.r.set(f"run:{run['run_id']}", json.dumps(run, default=str), ex=86400); self.r.zadd("runs:index", {run["run_id"]: run["submitted_at"]})
    def get(self, rid):
        v = self.r.get(f"run:{rid}"); return json.loads(v) if v else None
    def enqueue(self, rid): self.r.rpush("triage:queue", rid)
    def dequeue(self, timeout=1):
        v = self.r.blpop("triage:queue", timeout=timeout); return v[1] if v else None
    def depth(self): return self.r.llen("triage:queue")
    def cache_get(self, k):
        v = self.r.get(f"cache:{k}"); return json.loads(v) if v else None
    def cache_put(self, k, val): self.r.set(f"cache:{k}", json.dumps(val, default=str), ex=settings.CACHE_TTL_S)
    def add_feedback(self, fb): self.r.rpush("feedback", json.dumps(fb))
    def all_feedback(self): return [json.loads(x) for x in self.r.lrange("feedback", 0, -1)]
    def recent(self, n=200): return [self.get(i) for i in self.r.zrevrange("runs:index", 0, n - 1) if self.get(i)]

STORE = RedisStore() if settings.QUEUE_BACKEND == "redis" else MemStore()

def cache_key(req: AlertRequest, note: str):
    d = {"m": req.metric, "v": req.value, "t": req.threshold, "pg": req.pg_version, "r": req.host_role, "n": note.lower().strip(),
         "l": {k: req.labels[k] for k in sorted(req.labels)}, "pv": settings.PROMPT_VERSION, "mode": settings.AGENT_MODE}
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()

def new_run(req: AlertRequest, cid: str, client: str):
    rid = uuid.uuid4().hex[:16]
    run = {"run_id": rid, "correlation_id": cid, "client": client, "status": "queued", "request": req.model_dump(mode="json"),
           "submitted_at": time.time(), "started_at": None, "finished_at": None, "result": None, "trace": [], "prompt_version": settings.PROMPT_VERSION, "cached": False}
    return run

def execute(rid: str):
    run = STORE.get(rid)
    if not run: return
    corr_id.set(run["correlation_id"])
    run["status"], run["started_at"] = "running", time.time(); STORE.put(run)
    req = AlertRequest.model_validate(run["request"])
    try:
        clean, flags = guardrails.scan_input(req.oncall_note)
        if flags: log("guardrail triggered", flags=[f["kind"] for f in flags], run_id=rid)
        injected_only = any(f["kind"] == "prompt_injection" for f in flags) and len(clean) < 10 and len(req.oncall_note) > 20
        final = agents.run_investigation(req, clean, run["correlation_id"], flags)
        rec, outcome, ver = final.get("recommendation"), final.get("outcome", {}), final.get("verification")
        reason = outcome.get("escalate")
        if injected_only and not reason: reason = "on-call note contained only an injection attempt; nothing trustworthy to act on"; rec = None
        escalated = bool(reason) or rec is None
        routing = (ver or {}).get("routing") if (ver and ver.get("ok") and not escalated) else None
        if routing is None:
            routing = policy.route_for(req, True, reason or "unverified"); 
        res = {"status": "escalated" if escalated else "answered",
               "proposed_cause_class": None if escalated else rec["cause_class"],
               "hypothesis": None if escalated else rec["label"], "summary": None if escalated else rec["summary"],
               "steps": [] if escalated else rec["steps"], "citations": [] if escalated else rec["citations"],
               "alternatives": (rec or {}).get("alternatives") or [{"id": a["id"], "cause_class": a["cause_class"], "support": a.get("support")} for a in outcome.get("alternatives", [])],
               "escalation": {"required": True, "reason": reason} if escalated else {"required": False, "reason": None},
               **routing, "verification": {"ok": bool(ver and ver.get("ok")), "failures": (ver or {}).get("failures", []), "scores": (ver or {}).get("scores", {}), "attempts": final.get("attempt", 0) + 1},
               "degraded": bool(final.get("degraded")), "guardrails": [f["kind"] for f in final.get("flags", [])] + ([] if not injected_only else ["injection_only_note"]),
               "prompt_version": settings.PROMPT_VERSION, "agent_mode": settings.AGENT_MODE,
               "usage": {"prompt_tokens": final["tokens"]["prompt"], "completion_tokens": final["tokens"]["completion"], "cost_usd": round(final["llm_cost"], 6)},
               "steps_taken": final["steps"], "tool_calls": final["tool_calls"], "safety_note": "This system proposes only. It never executes remediation; the on-call engineer decides."}
        if final.get("degraded"): metrics.DEGRADED.inc()
        run["trace"] = [{k: v for k, v in t.items() if k != "cid"} for t in final["trace"]]
        run["result"], run["status"] = res, ("escalated" if escalated else "succeeded")
    except Exception as e:
        log("run failed", error=repr(e), run_id=rid)
        res_route = policy.route_for(req, True, "internal error")
        run["result"] = {"status": "failed", "error": f"{type(e).__name__}: {e}", "escalation": {"required": True, "reason": "run did not complete; no answer returned"}, **res_route}
        run["status"] = "failed"
    run["finished_at"] = time.time()
    dur = run["finished_at"] - run["submitted_at"]
    metrics.RUN_SEC.observe(dur); metrics.RUNS.labels(run["status"], (run["result"] or {}).get("route", "-")).inc()
    STORE.put(run)
    if settings.CACHE_RUNS and run["status"] in ("succeeded", "escalated"):
        STORE.cache_put(cache_key(req, req.oncall_note), {"result": run["result"], "trace": run["trace"]})
    log("run finished", run_id=rid, status=run["status"], seconds=round(dur, 3))

def worker_loop(stop: threading.Event):
    while not stop.is_set():
        rid = STORE.dequeue(timeout=1)
        if rid:
            try: execute(rid)
            except Exception as e: log("worker error", error=repr(e))
