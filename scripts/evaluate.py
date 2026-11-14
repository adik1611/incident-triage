"""Batch evaluation (L3, L2). Runs the fixed scenario set in-process and reports four metrics.
  python scripts/evaluate.py                      # agentic, current prompt
  python scripts/evaluate.py --compare-mode       # single_pass baseline vs agentic (the deliberate change)
  python scripts/evaluate.py --compare-prompts    # draft_v1 vs draft_v2
Metric definitions (computed here, TruLens-equivalent, no LLM judge so they are reproducible and free):
  groundedness       share of answer claims (steps + summary citations) whose cited id resolves to a passage that was actually retrieved in that run
  faithfulness       share of proposed steps whose content words are >=80% contained in the cited passage text
  context_relevance  share of retrieved passages (unique per run) that are relevant: contain >=1 required evidence keyword group of the expected hypothesis
  hallucination_rate share of ALL runs with an unresolved citation, an unsupported step, OR a confident answer where the corpus cannot answer (expected escalate)
Also reported: outcome accuracy, hypothesis accuracy, citation recall (expected passage appears among citations), route accuracy."""
import argparse, json, os, re, sys, time, datetime as dt
os.environ.setdefault("CACHE_RUNS", "0"); os.environ.setdefault("QUEUE_BACKEND", "memory")
sys.path.insert(0, os.getcwd())
from app.config import settings
settings.CACHE_RUNS = False
from app import runs, tools, contract, llm
from app.knowledge import BY_ID, coverage
from app.verifier import content_words, CITE

def one(sc):
    d = json.loads(json.dumps(sc["request"])); d["fired_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    req, errs = contract.validate(d, "eval")
    assert req, errs
    run = runs.new_run(req, "eval-" + sc["id"], "eval"); runs.STORE.put(run); t0 = time.time(); runs.execute(run["run_id"]); r = runs.STORE.get(run["run_id"])
    r["_secs"] = time.time() - t0; return r

def score(sc, run):
    res, exp = run["result"], sc["expected"]; tools.INDEX.ensure()
    retrieved = {}
    for t in run["trace"]:
        for p in t.get("passages", []) or []: retrieved[p["id"]] = tools.INDEX.by_id[p["id"]]["text"] if p["id"] in tools.INDEX.by_id else ""
    answered = res.get("status") == "answered"
    out = {"id": sc["id"], "answered": answered, "expected": exp["outcome"], "outcome_ok": answered == (exp["outcome"] == "answer"),
           "route": res.get("route"), "secs": round(run["_secs"], 3), "tool_calls": res.get("tool_calls"), "cost_usd": (res.get("usage") or {}).get("cost_usd", 0),
           "hyp": None, "hyp_ok": None, "recall": None, "ground": None, "faith": None, "relevance": None, "halluc": False}
    if exp["route"]: out["route_ok"] = res.get("route") == exp["route"]
    if exp["guardrail"]: out["guardrail_ok"] = exp["guardrail"] in (res.get("guardrails") or [])
    if answered:
        claims = [(s["text"], s["cite"]) for s in res["steps"]]
        cited = [c["id"] for c in res["citations"]]
        resolved = [1 if all(i in retrieved for i in cs) else 0 for _, cs in claims] + [1 if i in retrieved else 0 for i in cited] + [1 if m in retrieved else 0 for m in CITE.findall(res["summary"] or "")]
        out["ground"] = sum(resolved) / max(1, len(resolved))
        fa = []
        for txt, cs in claims:
            cw = content_words(re.sub(r"^(Proposed|Version note):\s*", "", txt)); src = set().union(*[content_words(retrieved.get(i, "")) for i in cs]) if cs else set()
            fa.append(1 if cw and len(cw & src) / len(cw) >= 0.8 else 0)
        out["faith"] = sum(fa) / max(1, len(fa))
        out["summary_cited"] = 1 if CITE.findall(res["summary"] or "") else 0
        out["hyp"] = next((h for h, v in BY_ID.items() if v["label"] == res["hypothesis"]), None)
        out["hyp_ok"] = out["hyp"] == exp["hypothesis"] if exp["hypothesis"] else False
        if exp["supporting_passages"]: out["recall"] = sum(1 for p in exp["supporting_passages"] if p in cited) / len(exp["supporting_passages"])
        if exp["hypothesis"]:
            h = BY_ID[exp["hypothesis"]]
            rel = [1 if any(any(w in t.lower() for w in g) for g in h["must_have"]) else 0 for t in retrieved.values()]
            out["relevance"] = sum(rel) / max(1, len(rel))
        out["halluc"] = out["ground"] < 1 or out["faith"] < 1 or exp["outcome"] == "escalate"
    return out

def mean(xs): xs = [x for x in xs if x is not None]; return round(sum(xs) / len(xs), 3) if xs else None

def evaluate(label):
    scs = json.load(open("eval/scenarios.json")); rows = [score(s, one(s)) for s in scs]
    ans = [r for r in rows if r["answered"]]; exp_ans = [r for r in rows if r["expected"] == "answer"]
    agg = {"label": label, "n": len(rows), "prompt_version": settings.PROMPT_VERSION, "agent_mode": settings.AGENT_MODE,
           "groundedness": mean([r["ground"] for r in ans]), "faithfulness": mean([r["faith"] for r in ans]),
           "context_relevance": mean([r["relevance"] for r in ans]), "hallucination_rate": round(sum(r["halluc"] for r in rows) / len(rows), 3),
           "outcome_accuracy": mean([1 if r["outcome_ok"] else 0 for r in rows]), "hypothesis_accuracy": mean([1 if r["hyp_ok"] else 0 for r in exp_ans]),
           "citation_recall": mean([r["recall"] if r["answered"] else 0 for r in exp_ans if r["recall"] is not None or not r["answered"]]),
           "route_accuracy": mean([1 if r.get("route_ok") else 0 for r in rows if "route_ok" in r]),
           "summary_cited_rate": mean([r.get("summary_cited") for r in ans]), "mean_tool_calls": mean([r["tool_calls"] for r in rows if r["tool_calls"] is not None]),
           "mean_cost_usd": mean([r["cost_usd"] for r in rows]), "s1_section_4_1": next(r for r in rows if r["id"] == "S01-section-4-1")}
    return agg, rows

def table(aggs):
    keys = ["groundedness", "faithfulness", "context_relevance", "hallucination_rate", "outcome_accuracy", "hypothesis_accuracy", "citation_recall", "route_accuracy", "summary_cited_rate", "mean_tool_calls"]
    s = "| metric | " + " | ".join(a["label"] for a in aggs) + " |\n|---|" + "---|" * len(aggs) + "\n"
    for k in keys: s += f"| {k} | " + " | ".join(str(a[k]) for a in aggs) + " |\n"
    s += "| S01 (section 4.1) | " + " | ".join(("answered " + str(a["s1_section_4_1"]["hyp"]) + " ✔" if a["s1_section_4_1"]["hyp_ok"] else ("escalated" if not a["s1_section_4_1"]["answered"] else "answered " + str(a["s1_section_4_1"]["hyp"]) + " ✘")) for a in aggs) + " |\n"
    return s

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--compare-mode", action="store_true"); ap.add_argument("--compare-prompts", action="store_true"); ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(); os.makedirs("eval/results", exist_ok=True); aggs = []; allrows = {}
    if a.compare_mode:
        for mode, lab in (("single_pass", "BEFORE: single-pass retrieval"), ("agentic", "AFTER: agentic multi-hop")):
            settings.AGENT_MODE = mode; g, rows = evaluate(lab); aggs.append(g); allrows[lab] = rows
    elif a.compare_prompts:
        settings.AGENT_MODE = "agentic"
        for pv in ("draft_v1", "draft_v2"):
            settings.PROMPT_VERSION = pv; g, rows = evaluate(pv); aggs.append(g); allrows[pv] = rows
    else:
        g, rows = evaluate("current"); aggs.append(g); allrows["current"] = rows
    print(table(aggs))
    if a.verbose:
        for lab, rows in allrows.items():
            print("\n##", lab)
            for r in rows: print(f"  {r['id']:22} expect={r['expected']:8} got={'answer' if r['answered'] else 'escalate':8} hyp={r['hyp']} ok={r['outcome_ok']} route={r['route']} recall={r['recall']}")
    name = "mode" if a.compare_mode else "prompts" if a.compare_prompts else "current"
    json.dump({"summary": aggs, "rows": allrows}, open(f"eval/results/{name}.json", "w"), indent=1, default=str)
    open(f"eval/results/{name}.md", "w").write(table(aggs))
