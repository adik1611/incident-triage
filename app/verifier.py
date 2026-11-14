"""Verifier + Router agent (A2, A3, F6). Independent of the investigator: it receives only the proposed answer and the
retrieved passage text, re-derives every check itself, and assigns severity/route. It can run as a separate service."""
import re
from . import metrics, policy, guardrails
from .knowledge import BY_ID, coverage

_W = re.compile(r"[a-z_][a-z_0-9]{2,}")
_STOP = set("the and for are with that this from not can may will when which then its has have been into than also only each does".split())
def content_words(t): return {w for w in _W.findall((t or "").lower()) if w not in _STOP}
CITE = re.compile(r"\[([A-Z]{2,}[\w.\-]*\d[\w.\-]*|RB-\d+|POL-[A-Z]+|PG\d+-[\w.\-]+)\]")

def verify_and_route(payload):
    req = payload["request"]; rec = payload.get("recommendation"); retrieved = payload.get("retrieved", {})
    esc = payload.get("escalation_reason")
    class R:  # lightweight request view for policy
        pass
    r = R(); r.metric, r.value, r.labels = req["metric"], req["value"], req.get("labels", {})
    out = {"ok": False, "failures": [], "scores": {}, "agent": "verifier-router"}
    if rec is None:
        out["routing"] = policy.route_for(r, True, esc or "no answer produced"); return out
    fails = []
    cites = rec.get("citations", [])
    ids = [c["id"] for c in cites]
    # V1 every citation resolves to retrieved text, and the quote really is from that text
    unresolved = [i for i in ids if i not in retrieved]
    if unresolved: fails.append({"check": "citation_resolves", "detail": f"not in retrieved set: {unresolved}"})
    for c in cites:
        t = retrieved.get(c["id"], {}).get("text", "")
        if t and c.get("quote") and c["quote"][:60] not in t: fails.append({"check": "quote_matches", "detail": c["id"]})
    # V2 version
    badv = [i for i in ids if i in retrieved and retrieved[i].get("version") not in (None, req["pg_version"])]
    if badv: fails.append({"check": "version_match", "detail": f"passages for other major version: {badv}"})
    # V3 steps faithful to cited passages
    sup_scores = []
    for s in rec.get("steps", []):
        bad = [i for i in s.get("cite", []) if i not in retrieved]
        if bad or not s.get("cite"):
            fails.append({"check": "step_citation", "detail": f"step cites unresolved/no passage: {s.get('text','')[:70]} -> {bad or 'none'}"}); sup_scores.append(0.0); continue
        cw = content_words(re.sub(r"^Proposed:\s*", "", s["text"]))
        src = set().union(*[content_words(retrieved[i]["text"]) for i in s["cite"]])
        sc = len(cw & src) / max(1, len(cw)); sup_scores.append(sc)
        if sc < 0.8: fails.append({"check": "step_supported", "detail": f"support {sc:.2f}: {s['text'][:70]}"})
    # V4 hypothesis covered by cited passages
    h = BY_ID.get(rec.get("hypothesis_id"))
    cov = 0.0
    if h:
        cov, _ = coverage(h, [retrieved[i]["text"] for i in ids if i in retrieved])
        if cov < 1.0: fails.append({"check": "hypothesis_covered", "detail": f"cited passages cover {cov:.0%} of required evidence for '{h['id']}'"})
    else: fails.append({"check": "hypothesis_known", "detail": str(rec.get("hypothesis_id"))})
    # V5 summary: citations resolve, content supported, no unauthorised claim
    summ = rec.get("summary", "")
    bad = [m for m in CITE.findall(summ) if m not in retrieved]
    if bad: fails.append({"check": "summary_citation", "detail": f"summary cites unresolved: {bad}"})
    sw = content_words(summ)
    pool = set().union(*[content_words(retrieved[i]["text"]) for i in ids if i in retrieved] or [set()]) | content_words(rec.get("label", "")) | content_words(rec.get("cause_class", "")) | {"proposals", "proposal", "engineer", "review", "supporting", "guidance", "steps", "below", "likely", "alert", "pattern", "most", "consistent", "cause", "on-call", "named", "cited", "stub", "response"}
    ssup = len(sw & pool) / max(1, len(sw)) if sw else 1.0
    out["scores"]["summary_support"] = round(ssup, 3)
    if ssup < 0.6: fails.append({"check": "summary_supported", "detail": f"only {ssup:.0%} of summary words appear in cited evidence"})
    _, ff = guardrails.filter_output(summ + " " + " ".join(s["text"] for s in rec.get("steps", [])))
    if ff: fails.append({"check": "authorised_claims_only", "detail": "output contained an unauthorised determination"})
    out["scores"].update({"citations_resolved": 1.0 if not unresolved else round(1 - len(unresolved) / max(1, len(ids)), 3),
                          "step_support_mean": round(sum(sup_scores) / len(sup_scores), 3) if sup_scores else 0.0, "hypothesis_coverage": cov})
    out["failures"] = fails; out["ok"] = not fails
    metrics.VERIFY.labels("pass" if out["ok"] else "fail").inc()
    out["routing"] = policy.route_for(r, False)
    return out
