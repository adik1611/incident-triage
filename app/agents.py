"""Investigator agent (LangGraph) + hand-off to the Verifier/Router agent (A1-A3, F3-F7).
Graph: classify -> plan -> act -> plan ... -> draft -> verify -> (end | plan with new strategy | escalate)."""
import re, time, uuid, concurrent.futures as cf
from typing import Any, Dict, TypedDict
from langgraph.graph import StateGraph, END
import httpx
from .config import settings
from . import tools, llm, metrics, guardrails, verifier as local_verifier
from .knowledge import HYPOTHESES, BY_ID, features, rank, coverage
from .logsetup import log

_POOL = cf.ThreadPoolExecutor(max_workers=16)
STRATEGIES = ["expansion", "keyword", "alternate_hypothesis"]

class State(TypedDict, total=False):
    req: dict; req_obj: Any; note: str; feats: dict; hyps: list; evidence: dict; queries: list; fetched: list
    phase: int; action: dict; steps: int; tool_calls: int; trace: list; attempt: int; excluded: list
    recommendation: dict; verification: dict; outcome: dict; deadline: float; degraded: bool; llm_cost: float
    tokens: dict; step_base: int; tool_base: int; started: float; focus: str; derived_done: bool; refs_done: bool; flags: list

def _span(state, kind, name, fn, **attrs):
    t0 = time.time(); err = None; res = None
    try: res = fn()
    except Exception as e: err = e
    dt = time.time() - t0
    metrics.STEP_SEC.labels(name).observe(dt)
    state["steps"] = state.get("steps", 0) + 1
    entry = {"n": state["steps"], "agent": attrs.pop("agent", "investigator"), "type": kind, "name": name, "latency_ms": round(dt * 1000, 2), "ts": time.time()}
    entry.update(attrs)
    if err: entry["error"] = f"{type(err).__name__}: {err}"
    state["trace"].append(entry)
    if err: raise err
    return res, entry

def _tool(state, name, fn, timeout=None, **inp):
    state["tool_calls"] = state.get("tool_calls", 0) + 1
    metrics.TOOL_CALLS.labels(name).inc()
    def run():
        fut = _POOL.submit(fn)
        try: return fut.result(timeout=timeout or settings.TOOL_TIMEOUT_S)
        except cf.TimeoutError: raise TimeoutError(f"tool {name} exceeded {timeout or settings.TOOL_TIMEOUT_S}s")
    res, entry = _span(state, "tool", name, run, input=inp)
    return res, entry

def _add_evidence(state, passages, query):
    for p in passages:
        e = state["evidence"].get(p["id"])
        if e is None: state["evidence"][p["id"]] = {**p, "queries": [query]}
        else:
            e["queries"].append(query)
            if (p.get("score") or 0) > (e.get("score") or 0): e["score"] = p["score"]

def _kw_query(f):
    words = re.findall(r"[a-z_]{4,}", f["note"])[:8]
    return f"{f['metric'].replace('_', ' ')} {' '.join(words)} runbook symptoms"

# ---------------- nodes ----------------
def classify(state: State):
    req = state["req_obj"]
    f = features(req, state["note"])
    hyps = rank(f)
    _span(state, "decision", "classify_symptom", lambda: None, input={"metric": req.metric, "note": state["note"][:200]},
          output={"entities": {k: f[k] for k in ("metric", "host_role", "value", "pg_version", "sizes_flat", "sizes_growing")}, "hypotheses": hyps[:4]})
    return {"feats": f, "hyps": hyps}

def _focus(state):
    ex = set(state.get("excluded", []))
    cands = [h for h in state["hyps"] if h["id"] not in ex]
    s = state.get("attempt", 0)
    if STRATEGIES[min(s, 2)] == "alternate_hypothesis" and len(cands) > 1: return cands[0]  # top remaining after exclusion
    return cands[0] if cands else None

def plan(state: State):
    now = time.time()
    if now > state["deadline"]: return {"action": {"tool": None, "why": "wall_clock_bound"}, "outcome": {"escalate": "run exceeded wall-clock bound"}}
    # Budgets apply PER ATTEMPT (so a verification retry is not starved), with a global hard cap across attempts.
    used_steps = state["steps"] - state.get("step_base", 0); used_tools = state.get("tool_calls", 0) - state.get("tool_base", 0)
    if state["steps"] >= settings.MAX_STEPS * (settings.MAX_VERIFY_RETRIES + 1):
        return {"action": {"tool": None, "why": "global_step_bound"}, "outcome": {"escalate": "global step bound reached"}}
    if used_steps >= settings.MAX_STEPS or used_tools >= settings.MAX_TOOL_CALLS:
        why = "step_bound" if used_steps >= settings.MAX_STEPS else "tool_bound"
        if state["evidence"]: return {"action": {"tool": None, "why": why}}          # stop searching, draft from what was found; the verifier decides
        return {"action": {"tool": None, "why": why}, "outcome": {"escalate": f"{why} reached before any evidence was retrieved"}}
    v = state["feats"]["pg_version"]
    if state["feats"]["pg_version"] != settings.COVERED_PG_VERSION:
        return {"action": {"tool": None, "why": "version"}, "outcome": {"escalate": f"PostgreSQL {v} is outside the corpus (only {settings.COVERED_PG_VERSION} is indexed)"}}
    if settings.AGENT_MODE == "single_pass":
        if not state["queries"]:
            q = f"{state['feats']['metric'].replace('_', ' ')} {state['note']}".strip()
            return {"action": {"tool": "search_corpus", "args": {"query": q, "k": 4}, "why": "single-pass baseline"}}
        return {"action": {"tool": None, "why": "baseline done"}}
    focus = _focus(state)
    if focus is None: return {"action": {"tool": None, "why": "no hypotheses"}, "outcome": {"escalate": "no hypothesis fits the alert"}}
    strat = STRATEGIES[min(state.get("attempt", 0), 2)]
    h = BY_ID[focus["id"]]
    qs = [_kw_query(state["feats"])] if strat == "keyword" else list(h["queries"])
    pending = [q for q in qs if q not in state["queries"]]
    if pending:
        return {"action": {"tool": "search_corpus", "args": {"query": pending[0], "k": 4}, "why": f"{strat}: retrieve guidance for '{h['id']}'"}, "focus": h["id"]}
    # step 2: do any other provisions modify what was found? follow references in the best passages
    top = sorted(state["evidence"].values(), key=lambda e: -(e.get("score") or 0))[:3]
    if not state.get("refs_done"):
        for e in top:
            for ref in tools.find_references(e["text"]):
                if ref not in state["evidence"] and ref not in state["fetched"] and ref != e["id"]:
                    return {"action": {"tool": "get_clause", "args": {"clause_id": ref}, "why": f"{e['id']} cross-references {ref}"}, "focus": h["id"]}
        return {"action": {"tool": "noop_refs"}, "refs_done": True, "focus": h["id"]}
    # step 3: search again with a query derived from what was read
    if not state.get("derived_done"):
        cnt = {}
        prior = " ".join(state["queries"])
        for e in top:
            for i in tools.identifiers(e["text"]):
                if i not in prior and len(i) > 6: cnt[i] = cnt.get(i, 0) + 1
        ids = [i for i, _ in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:3]]
        if ids: return {"action": {"tool": "search_corpus", "args": {"query": " ".join(ids) + " " + h["label"], "k": 3}, "why": f"derived from retrieved text: {ids}"}, "derived_done": True, "focus": h["id"]}
        return {"action": {"tool": "noop_derived"}, "derived_done": True, "focus": h["id"]}
    return {"action": {"tool": None, "why": "enough evidence gathered"}, "focus": h["id"]}

def route_after_plan(state):
    a = state["action"]
    if state.get("outcome", {}).get("escalate"): return "escalate"
    if a.get("tool") in ("noop_refs", "noop_derived"): return "plan"
    return "act" if a.get("tool") else "draft"

def act(state: State):
    a = state["action"]; v = state["feats"]["pg_version"]
    upd: Dict[str, Any] = {}
    if a["tool"] == "search_corpus":
        q = a["args"]["query"]
        res, ent = _tool(state, "search_corpus", lambda: tools.search_corpus(q, a["args"].get("k", 4), version=v), query=q, why=a.get("why"))
        ent["passages"] = [{"id": r["id"], "score": r["score"]} for r in res]; ent["query"] = q
        state["queries"].append(q); _add_evidence(state, res, q)
    elif a["tool"] == "get_clause":
        cid = a["args"]["clause_id"]
        res, ent = _tool(state, "get_clause", lambda: tools.get_clause(cid, v), clause_id=cid, why=a.get("why"))
        state["fetched"].append(cid)
        if res:
            res["score"] = 0.0; ent["passages"] = [{"id": res["id"], "score": None}]; _add_evidence(state, [res], f"clause:{cid}")
            state["evidence"][cid]["score"] = max(state["evidence"][cid].get("score") or 0, 0.2)  # fetched by explicit cross-reference
        else: ent["passages"] = []
    return upd

def _sentences(text):
    parts = re.split(r"(?<=[.;])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]
ACTION_CUE = re.compile(r"\b(query|use|check|set|remove|compare|restart|drop|identify|confirm|look|introduce|cancel|review|SELECT|raise|enable|limit|find|monitor|investigate|fix)\b", re.I)

def _step_sentences(e):
    """Extractive steps: runbooks contribute their Confirmation items and First response; docs contribute action sentences."""
    out = []
    if e["kind"] == "runbook":
        for line in e["text"].splitlines():
            if line.startswith("Confirmation:"):
                for it in re.split(r"\(\d\)\s*", line[len("Confirmation:"):]):
                    it = it.strip().rstrip(".;")
                    if len(it) > 12: out.append("Confirm: " + it + ".")
            elif line.startswith("First response"):
                body = line.split(":", 1)[1]
                out += [x for x in _sentences(body) if len(x) > 20]
        return out[:8]
    for s in _sentences(e["text"]):
        if (ACTION_CUE.search(s) or re.search(r"version 16|PostgreSQL 16", s)) and len(s) > 30: out.append(s)
    return out[:2]

def _decide(state):
    ev = state["evidence"]; texts_by = {i: e["text"] for i, e in ev.items()}
    ex = set(state.get("excluded", []))
    ranked = []
    for h in state["hyps"]:
        if h["id"] in ex: continue
        cov, _ = coverage(BY_ID[h["id"]], list(texts_by.values()))
        support = cov if settings.AGENT_MODE == "single_pass" else 0.6 * h["prior"] + 0.4 * cov
        ranked.append({**h, "coverage": round(cov, 3), "support": round(support, 3)})
    if settings.AGENT_MODE == "single_pass":
        ranked = [{**r} for r in sorted(ranked + [], key=lambda x: -x["support"])]
        ranked = [r for r in ranked if r["support"] >= 1.0]
        # baseline has no symptom classification: any hypothesis fully covered by retrieved text competes
    else:
        ranked.sort(key=lambda x: -x["support"])
    return ranked

def _pick_citations(h, ev, pg_version):
    cands = []
    for e in ev.values():
        t = e["text"].lower()
        hit = sum(any(w in t for w in g) for g in h["must_have"])
        if hit and (e["version"] in (None, pg_version)):
            cands.append((hit + (0.5 if e["kind"] == "runbook" else 0) + (e.get("score") or 0), e))
    cands.sort(key=lambda x: -x[0])
    chosen, covered = [], [False] * len(h["must_have"])
    for _, e in cands:
        t = e["text"].lower()
        gain = [any(w in t for w in g) for g in h["must_have"]]
        if any(g and not c for g, c in zip(gain, covered)) or (e["kind"] == "runbook" and not any(c["kind"] == "runbook" for c in chosen)):
            chosen.append(e); covered = [c or g for c, g in zip(covered, gain)]
    for _, e in cands:
        t = e["text"].lower()
        if e not in chosen and sum(any(w in t for w in g) for g in h["must_have"]) >= 2: chosen.append(e)
    return chosen[:5]

def draft(state: State):
    ev = state["evidence"]; req = state["req_obj"]
    ranked = _decide(state)
    if not ranked: return {"outcome": {"escalate": "retrieved guidance does not support any known cause class"}}
    top = ranked[0]
    if settings.AGENT_MODE == "single_pass":
        if len(ranked) > 1 and ranked[1]["cause_class"] != top["cause_class"]:
            return {"outcome": {"escalate": f"ambiguous: {top['id']} vs {ranked[1]['id']}"}}
    else:
        if top["support"] < 0.55: return {"outcome": {"escalate": f"insufficient support (best '{top['id']}' = {top['support']})", "alternatives": ranked[:3]}}
        if len(ranked) > 1 and ranked[1]["cause_class"] != top["cause_class"] and top["support"] - ranked[1]["support"] < settings.AMBIGUITY_MARGIN:
            return {"outcome": {"escalate": f"symptom maps to several cause classes with similar support: {top['cause_class']} ({top['id']}={top['support']}) vs {ranked[1]['cause_class']} ({ranked[1]['id']}={ranked[1]['support']})", "alternatives": ranked[:3]}}
    h = BY_ID[top["id"]]
    cites = _pick_citations(h, ev, req.pg_version)
    if not cites: return {"outcome": {"escalate": "no citable passage for the leading hypothesis"}}
    steps = []
    for e in sorted(cites, key=lambda e: e["kind"] != "runbook"):
        for s in _step_sentences(e):
            s = s.rstrip(" ;") + ("" if s.rstrip(" ;").endswith(".") else "."); steps.append({"text": ("" if s.startswith("Confirm:") else "Version note: " if re.search(r"version 16|PostgreSQL 16", s) and e["kind"] != "runbook" else "Proposed: ") + s.rstrip(), "cite": [e["id"]]})
    seen, uniq = set(), []
    for st in steps:
        if st["text"] not in seen: seen.add(st["text"]); uniq.append(st)
    steps = uniq[:9]
    ids = [c["id"] for c in cites]
    ctx = {"label": h["label"], "cause_class": h["cause_class"], "cite_ids": ids, "prompt_version": settings.PROMPT_VERSION}
    evidence_block = "\n".join(f"[{e['id']}] {e['text'][:400]}" for e in cites)
    system = llm.load_prompt(settings.PROMPT_VERSION).format(pg_version=req.pg_version)
    user = f"<evidence>\n{evidence_block}\n</evidence>\n<untrusted_note>{state['note'][:600]}</untrusted_note>\nHypothesis: {h['label']}"
    degraded, summary, lc = state.get("degraded", False), "", None
    try:
        lc, _ = _span(state, "llm", "draft_summary", lambda: llm.complete(system, user, ctx, step="draft_summary"),
                      prompt_version=settings.PROMPT_VERSION, input={"chars": len(user)})
        summary = lc["text"]; state["trace"][-1].update({"prompt_tokens": lc["prompt_tokens"], "completion_tokens": lc["completion_tokens"], "cost_usd": lc["cost_usd"], "retries": lc["retries"]})
        state["tokens"]["prompt"] += lc["prompt_tokens"]; state["tokens"]["completion"] += lc["completion_tokens"]; state["llm_cost"] += lc["cost_usd"]
    except Exception as e:
        degraded = True
        summary = f"Likely {h['label']} ({h['cause_class']}). Supporting guidance: " + ", ".join(f"[{i}]" for i in ids) + ". Steps below are proposals for the on-call engineer to review."
        state["trace"].append({"n": state["steps"], "agent": "investigator", "type": "degrade", "name": "extractive_fallback", "error": str(e), "latency_ms": 0, "ts": time.time()})
        log("llm unavailable, degraded to extractive summary", error=str(e))
    if getattr(state.get("req_obj"), "labels", {}).get("demo_fault") == "unsupported_citation" and state.get("attempt", 0) == 0 and settings.DEMO_MODE:
        steps.insert(0, {"text": "Proposed: Drop the replication slot immediately and restart the cluster to reclaim WAL space.", "cite": ["PG16-99.9"]})
    summary, f1 = guardrails.filter_output(summary)
    rec = {"hypothesis_id": h["id"], "label": h["label"], "cause_class": h["cause_class"], "summary": summary, "steps": steps,
           "citations": [{"id": e["id"], "title": e["title"], "kind": e["kind"], "version": e["version"], "quote": e["text"][:220]} for e in cites],
           "support": top["support"], "alternatives": [{"id": r["id"], "cause_class": r["cause_class"], "support": r["support"]} for r in ranked[1:3]]}
    return {"recommendation": rec, "degraded": degraded, "flags": state.get("flags", []) + f1}

def verify_node(state: State):
    rec = state["recommendation"]
    payload = {"request": state["req"], "recommendation": rec, "retrieved": {i: {"text": e["text"], "version": e["version"]} for i, e in state["evidence"].items()}}
    def call():
        if settings.VERIFIER_URL:
            r = httpx.post(settings.VERIFIER_URL.rstrip("/") + "/verify", json=payload, timeout=settings.TOOL_TIMEOUT_S, headers={"X-Correlation-Id": state["trace"][0].get("cid", "")})
            r.raise_for_status(); return r.json()
        return local_verifier.verify_and_route(payload)
    try:
        v, ent = _span(state, "verify", "verifier_agent", call, agent="verifier-router", input={"citations": [c["id"] for c in rec["citations"]]})
    except Exception as e:
        return {"verification": {"ok": False, "failures": [{"check": "verifier_unavailable", "detail": str(e)}], "unavailable": True}, "outcome": {"escalate": "verifier unavailable: refusing to return an unverified answer"}}
    ent.update({"ok": v["ok"], "failures": v["failures"], "scores": v.get("scores")})
    return {"verification": v}

def route_after_verify(state):
    v = state["verification"]
    if v.get("unavailable"): return "escalate"
    if v["ok"]: return END
    return "retry" if state.get("attempt", 0) < settings.MAX_VERIFY_RETRIES else "escalate"

def retry(state: State):
    ex = list(state.get("excluded", []))
    nxt = state.get("attempt", 0) + 1
    fails = [f["check"] for f in state["verification"]["failures"]]
    if nxt == 2 and state["recommendation"]: ex.append(state["recommendation"]["hypothesis_id"])
    state["trace"].append({"n": state["steps"], "agent": "investigator", "type": "decision", "name": "retry_with_new_strategy", "latency_ms": 0, "ts": time.time(),
                           "output": {"failed_checks": fails, "next_strategy": STRATEGIES[min(nxt, 2)]}})
    return {"attempt": nxt, "excluded": ex, "step_base": state["steps"], "tool_base": state.get("tool_calls", 0), "recommendation": None, "verification": None, "refs_done": False, "derived_done": False, "outcome": {}}

def escalate(state: State):
    reason = state.get("outcome", {}).get("escalate")
    if not reason and state.get("verification"):
        reason = "verification failed after retries: " + "; ".join(f"{f['check']}" for f in state["verification"]["failures"])
    state["trace"].append({"n": state["steps"], "agent": "investigator", "type": "decision", "name": "escalate_to_human", "latency_ms": 0, "ts": time.time(), "output": {"reason": reason}})
    return {"outcome": {**state.get("outcome", {}), "escalate": reason or "unspecified"}, "recommendation": None}

def _keep(fn):
    def wrapped(state):
        out = fn(state) or {}
        out = dict(out); out["steps"] = state.get("steps", 0); out["tool_calls"] = state.get("tool_calls", 0)
        return out
    wrapped.__name__ = fn.__name__
    return wrapped

def build_graph():
    g = StateGraph(State)
    for n, f in [("classify", classify), ("plan", plan), ("act", act), ("draft", draft), ("verify", verify_node), ("retry", retry), ("escalate", escalate)]: g.add_node(n, _keep(f))
    g.set_entry_point("classify"); g.add_edge("classify", "plan")
    g.add_conditional_edges("plan", route_after_plan, {"act": "act", "plan": "plan", "draft": "draft", "escalate": "escalate"})
    g.add_edge("act", "plan")
    g.add_conditional_edges("draft", lambda s: "escalate" if s.get("outcome", {}).get("escalate") else "verify", {"verify": "verify", "escalate": "escalate"})
    g.add_conditional_edges("verify", route_after_verify, {END: END, "retry": "retry", "escalate": "escalate"})
    g.add_edge("retry", "plan"); g.add_edge("escalate", END)
    return g.compile()

GRAPH = build_graph()

def run_investigation(req_obj, note_clean, correlation_id, flags=None):
    t0 = time.time()
    init: State = {"req": req_obj.model_dump(mode="json"), "req_obj": req_obj, "note": note_clean, "evidence": {}, "queries": [], "fetched": [],
                   "phase": 0, "steps": 0, "tool_calls": 0, "trace": [{"n": 0, "agent": "orchestrator", "type": "start", "name": "run_start", "cid": correlation_id, "ts": t0, "latency_ms": 0}],
                   "attempt": 0, "excluded": [], "deadline": t0 + settings.MAX_RUN_SECONDS, "degraded": False, "llm_cost": 0.0,
                   "tokens": {"prompt": 0, "completion": 0}, "flags": list(flags or []), "started": t0, "refs_done": False, "derived_done": False}
    final = GRAPH.invoke(init, {"recursion_limit": 60})
    metrics.STEPS_PER_RUN.observe(final["steps"]); metrics.TOOLS_PER_RUN.observe(final["tool_calls"])
    return final
