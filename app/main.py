"""FastAPI API (F1, F8, A4, D1, D7). Submission is asynchronous: POST returns a run id, caller polls the status endpoint.
Why async + polling: a run makes several tool/model calls and takes tens of seconds; holding an HTTP request open that long
breaks proxies/timeouts and couples client lifetime to run lifetime. Polling needs no inbound endpoint on the caller (unlike callbacks)."""
import os, threading, time, uuid, json, collections
from fastapi import FastAPI, Request, Response, HTTPException, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
from .config import settings
from . import metrics, runs, tools, contract, llm
from .logsetup import log, corr_id

app = FastAPI(title="Incident Triage & Runbook Assistant", version="1.0",
              description="Proposes cause class, first-response steps and routing for PostgreSQL alerts. Never executes remediation.")
STOP = threading.Event(); THREADS = []
_buckets = collections.defaultdict(lambda: [float(settings.RATE_LIMIT_PER_MIN), time.time()])
_blk = threading.Lock()

def _tracing():
    if not settings.OTEL_ENDPOINT: return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        tp = TracerProvider(); tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_ENDPOINT + "/v1/traces"))); trace.set_tracer_provider(tp)
    except Exception as e: log("otel disabled", error=str(e))

@app.on_event("startup")
def startup():
    tools.INDEX.ensure(); _tracing()
    if settings.QUEUE_BACKEND == "memory":
        for _ in range(settings.LOCAL_WORKERS):
            t = threading.Thread(target=runs.worker_loop, args=(STOP,), daemon=True); t.start(); THREADS.append(t)
    log("api started", workers=len(THREADS), backend=settings.QUEUE_BACKEND, provider=settings.LLM_PROVIDER)

@app.on_event("shutdown")
def shutdown(): STOP.set()

@app.middleware("http")
async def mw(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex[:12]
    corr_id.set(cid); t0 = time.time()
    resp = await call_next(request)
    path = request.url.path if not request.url.path.startswith("/v1/runs/") else "/v1/runs/{id}"
    metrics.HTTP.labels(path, str(resp.status_code)).inc(); metrics.HTTP_SEC.labels(path).observe(time.time() - t0)
    resp.headers["X-Correlation-Id"] = cid
    return resp

def _rate_ok(client):
    with _blk:
        b = _buckets[client]; now = time.time()
        b[0] = min(settings.RATE_LIMIT_PER_MIN, b[0] + (now - b[1]) * settings.RATE_LIMIT_PER_MIN / 60.0); b[1] = now
        if b[0] >= 1: b[0] -= 1; return True
        return False

@app.post("/v1/runs", status_code=202, summary="Submit an alert + on-call note")
async def submit(request: Request, x_client_id: Optional[str] = Header(default=None)):
    cid = corr_id.get(); client = x_client_id or (request.client.host if request.client else "anon")
    if not _rate_ok(client):
        metrics.RATELIM.inc()
        return JSONResponse({"error": "rate_limited", "detail": f"limit {settings.RATE_LIMIT_PER_MIN}/min per client"}, status_code=429, headers={"Retry-After": "10"})
    try: payload = await request.json()
    except Exception: payload = (await request.body()).decode("utf-8", "replace")[:2000]
    req, errs = contract.validate(payload, cid)       # D1: validation BEFORE any agent starts
    if errs:
        metrics.QUARANTINED.inc(); log("request quarantined", errors=len(errs))
        return JSONResponse({"error": "validation_failed", "quarantined": True, "agent_run_started": False, "errors": errs, "correlation_id": cid}, status_code=422)
    run = runs.new_run(req, cid, client)
    if settings.CACHE_RUNS:
        hit = runs.STORE.cache_get(runs.cache_key(req, req.oncall_note))
        if hit:
            metrics.CACHE.labels("run", "hit").inc()
            run.update({"status": "escalated" if hit["result"].get("status") == "escalated" else "succeeded", "result": hit["result"], "trace": hit["trace"], "cached": True,
                        "started_at": time.time(), "finished_at": time.time()})
            runs.STORE.put(run); metrics.RUNS.labels(run["status"], hit["result"].get("route", "-")).inc(); metrics.RUN_SEC.observe(0.001)
            return {"run_id": run["run_id"], "status": run["status"], "cached": True, "status_url": f"/v1/runs/{run['run_id']}", "correlation_id": cid}
        metrics.CACHE.labels("run", "miss").inc()
    runs.STORE.put(run); runs.STORE.enqueue(run["run_id"])
    return {"run_id": run["run_id"], "status": "queued", "cached": False, "status_url": f"/v1/runs/{run['run_id']}", "correlation_id": cid}

def _public(run, full=False):
    d = {k: run[k] for k in ("run_id", "correlation_id", "status", "submitted_at", "started_at", "finished_at", "prompt_version", "cached")}
    d["latency_s"] = round(run["finished_at"] - run["submitted_at"], 3) if run.get("finished_at") else None
    d["result"] = run.get("result"); d["request"] = run["request"]
    if full: d["trace"] = run.get("trace")
    return d

@app.get("/v1/runs/{run_id}", summary="Poll run status/result")
def get_run(run_id: str):
    run = runs.STORE.get(run_id)
    if not run: raise HTTPException(404, "unknown run_id")
    return _public(run)

@app.get("/v1/runs/{run_id}/trace", summary="Full reconstruction of a run: tool calls, queries, passages, prompt version, latency, cost, verification")
def get_trace(run_id: str):
    run = runs.STORE.get(run_id)
    if not run: raise HTTPException(404, "unknown run_id")
    return _public(run, full=True)

class Feedback(BaseModel):
    satisfactory: bool
    reason: str = Field(default="", max_length=500)

@app.post("/v1/runs/{run_id}/feedback")
def feedback(run_id: str, fb: Feedback):
    run = runs.STORE.get(run_id)
    if not run: raise HTTPException(404, "unknown run_id")
    runs.STORE.add_feedback({"run_id": run_id, "satisfactory": fb.satisfactory, "reason": fb.reason, "ts": time.time(), "route": (run.get("result") or {}).get("route")})
    metrics.FEEDBACK.labels("up" if fb.satisfactory else "down").inc()
    return {"recorded": True}

@app.get("/v1/feedback/summary")
def fb_summary():
    f = runs.STORE.all_feedback(); up = sum(1 for x in f if x["satisfactory"])
    return {"total": len(f), "satisfactory": up, "rate": round(up / len(f), 3) if f else None,
            "reasons": collections.Counter(x["reason"] for x in f if not x["satisfactory"] and x["reason"]).most_common(5)}

@app.get("/v1/stats", summary="Aggregates for the dashboard panel")
def stats():
    rs = [r for r in runs.STORE.recent(500) if r.get("finished_at")]
    lat = sorted(r["finished_at"] - r["submitted_at"] for r in rs if not r.get("cached"))
    def pct(p): return round(lat[min(len(lat) - 1, int(p * len(lat)))], 3) if lat else None
    done = [r for r in rs if r["status"] != "failed"]
    tr = [r for r in rs if r.get("result") and r["result"].get("usage")]
    cost = sum(r["result"]["usage"]["cost_usd"] for r in tr if not r.get("cached"))
    return {"runs": len(rs), "task_success_rate": round(len(done) / len(rs), 3) if rs else None, "answered": sum(1 for r in rs if r["status"] == "succeeded"),
            "escalated": sum(1 for r in rs if r["status"] == "escalated"), "failed": sum(1 for r in rs if r["status"] == "failed"),
            "p50_s": pct(0.5), "p95_s": pct(0.95), "avg_steps": round(sum(r["result"].get("steps_taken", 0) for r in tr) / len(tr), 2) if tr else None,
            "avg_tool_calls": round(sum(r["result"].get("tool_calls", 0) for r in tr) / len(tr), 2) if tr else None,
            "cost_per_100_runs_usd": round(cost / max(1, len([r for r in tr if not r.get("cached")])) * 100, 4), "queue_depth": runs.STORE.depth(),
            "breaker": llm.breaker.state, "index": tools.INDEX.manifest, "uptime_s": round(time.time() - metrics.START)}

@app.get("/v1/contract", summary="Published data contract (JSON Schema)")
def contract_schema(): return contract.AlertRequest.model_json_schema()

@app.get("/v1/quarantine", summary="Recently quarantined requests with field-level errors")
def quarantine_list(limit: int = 20):
    if not os.path.exists(settings.QUARANTINE_FILE): return []
    lines = open(settings.QUARANTINE_FILE).read().splitlines()[-limit:]
    return [json.loads(l) for l in reversed(lines)]

@app.get("/v1/examples")
def examples():
    return json.load(open("eval/ui_examples.json"))

@app.post("/admin/fault/llm", summary="Demo only: make the model provider fail (circuit-breaker demonstration)")
def fault(body: dict):
    if not settings.DEMO_MODE: raise HTTPException(403, "demo mode off")
    if settings.LLM_PROVIDER == "openai_compat" and settings.LLM_BASE_URL:
        import httpx
        httpx.post(settings.LLM_BASE_URL.replace("/v1", "") + "/admin/fault", json=body, timeout=3)
    else:
        os.makedirs("data", exist_ok=True)
        if body.get("down"): open(llm.FAULT_FILE, "w").write("1")
        elif os.path.exists(llm.FAULT_FILE): os.remove(llm.FAULT_FILE)
    return {"llm_down": bool(body.get("down")), "breaker": llm.breaker.state}

@app.get("/health")
def health(): return {"status": "ok", "uptime_s": round(time.time() - metrics.START)}

@app.get("/ready")
def ready():
    ok = tools.INDEX.mat is not None
    return JSONResponse({"ready": ok, "index": tools.INDEX.manifest}, status_code=200 if ok else 503)

@app.get("/metrics")
def prom():
    body, ct = metrics.render(); return Response(body, media_type=ct)

app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/", include_in_schema=False)
def index(): return FileResponse("static/index.html")
