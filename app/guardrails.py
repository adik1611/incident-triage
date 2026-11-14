"""Prompt-injection, toxicity and output guardrails (L4)."""
import re
from . import metrics

INJECTION = [
    r"ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions?|prompts?|rules?)",
    r"disregard (all |any |the |your )?(previous|prior|above|earlier|system)?\s*(instructions?|prompts?|rules?)",
    r"(reveal|print|show|repeat|leak) (me )?(your|the) (system )?(prompt|instructions?|api key|secret|credentials?)",
    r"you are now\b", r"\bnew instructions?\s*:", r"\bsystem prompt\b", r"\bdeveloper mode\b", r"\bjailbreak\b",
    r"pretend (to be|you are)", r"act as (an? )?(unrestricted|root|admin)",
    r"<\s*/?\s*(system|assistant|im_start|im_end)\s*>", r"\[\s*(system|inst)\s*\]",
    r"(do not|don't|never) escalate", r"(mark|set|classify|route)( this| it)?( as| to)? (sev ?3|sev-3|self-serve|low severity)",
    r"(run|execute|apply) (the following|this) (command|sql|script)",
]
_inj = [re.compile(p, re.I) for p in INJECTION]
TOXIC = ["idiot", "stupid", "moron", "shut up", "fuck", "shit", "bastard", "dumbass", "asshole", "bitch", "damn you"]
_tox = re.compile(r"\b(" + "|".join(re.escape(t) for t in TOXIC) + r")\b", re.I)

def scan_input(text: str):
    """Returns (clean_text, flags). Injection sentences are removed; toxic words masked."""
    flags = []
    if not text: return "", flags
    sents = re.split(r"(?<=[.!?\n])\s+", text)
    kept, removed = [], []
    for s in sents:
        if any(p.search(s) for p in _inj): removed.append(s)
        else: kept.append(s)
    clean = " ".join(kept).strip()
    if removed:
        flags.append({"kind": "prompt_injection", "removed": [r[:160] for r in removed]}); metrics.GUARD.labels("prompt_injection").inc()
    if _tox.search(clean):
        clean = _tox.sub("[masked]", clean)
        flags.append({"kind": "toxicity_input"}); metrics.GUARD.labels("toxicity_input").inc()
    return clean, flags

FORBIDDEN_OUT = [
    (re.compile(r"\bI (have |already )?(executed|ran|dropped|terminated|restarted|deleted|killed|applied|fixed)\b[^.]*\.?", re.I), "[removed: system never executes remediations]"),
    (re.compile(r"\b(has|have) been (executed|dropped|terminated|restarted|deleted|killed|applied)\b[^.]*\.?", re.I), "[removed: system never executes remediations]"),
    (re.compile(r"\broot cause (is|was) (confirmed|certain)\b[^.]*\.?", re.I), "[removed: only on-call may confirm a root cause]"),
    (re.compile(r"\b(incident|issue|alert) (is |has been )?(resolved|closed|fixed)\b[^.]*\.?", re.I), "[removed: only on-call may close an incident]"),
    (re.compile(r"\b(safe to ignore|no action (is )?needed|false positive)\b", re.I), "[removed: not an authorised determination]"),
]

def filter_output(text: str):
    flags = []
    out = text or ""
    for pat, repl in FORBIDDEN_OUT:
        if pat.search(out):
            out = pat.sub(repl, out); flags.append({"kind": "output_unauthorised_claim"}); metrics.GUARD.labels("output_filter").inc()
    if _tox.search(out):
        out = _tox.sub("[masked]", out); flags.append({"kind": "toxicity_output"}); metrics.GUARD.labels("toxicity_output").inc()
    return out, flags
