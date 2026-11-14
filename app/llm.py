"""Model access behind an interface (A5, L5). Providers: offline (deterministic) | openai_compat.
Timeouts, bounded retry with exponential backoff, circuit breaker, per-call token/cost accounting."""
import os, random, threading, time
import httpx
from .config import settings
from . import metrics

FAULT_FILE = os.getenv("LLM_FAULT_FILE", "data/fault_llm")

class BreakerOpen(Exception): pass
class LLMError(Exception): pass

class CircuitBreaker:
    def __init__(self, fails, reset_s):
        self.fails, self.reset_s = fails, reset_s
        self.count, self.opened_at, self.lock = 0, None, threading.Lock()
    @property
    def state(self):
        with self.lock:
            if self.opened_at is None: return "closed"
            return "half_open" if time.time() - self.opened_at >= self.reset_s else "open"
    def allow(self): return self.state != "open"
    def ok(self):
        with self.lock: self.count, self.opened_at = 0, None
        metrics.BREAKER.set(0)
    def fail(self):
        with self.lock:
            self.count += 1
            if self.count >= self.fails or self.opened_at is not None:
                self.opened_at = time.time()
        if self.opened_at is not None: metrics.BREAKER.set(1)

breaker = CircuitBreaker(settings.BREAKER_FAILS, settings.BREAKER_RESET_S)

def est_tokens(text): return max(1, int(len(text.split()) * 1.3))

def _offline(system, user, ctx):
    """Deterministic stand-in for a model: renders a summary from structured context using the prompt version."""
    if os.path.exists(FAULT_FILE): raise LLMError("injected provider fault")
    c = ctx or {}
    ids = ", ".join(f"[{i}]" for i in c.get("cite_ids", []))
    if c.get("prompt_version") == "draft_v1":
        return f"Likely cause: {c.get('label', 'unknown')}."
    return (f"The alert pattern is most consistent with {c.get('label', 'an unknown cause')} ({c.get('cause_class', '?')}). "
            f"Supporting guidance: {ids}. Steps below are proposals for the on-call engineer to review.")

def _compat(system, user, ctx):
    r = httpx.post(settings.LLM_BASE_URL.rstrip("/") + "/chat/completions",
                   headers=({"Authorization": f"Bearer {settings.LLM_API_KEY}"} if settings.LLM_API_KEY else {}),
                   json={"model": settings.LLM_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                         "temperature": 0}, timeout=settings.LLM_TIMEOUT_S)
    r.raise_for_status()
    j = r.json(); u = j.get("usage", {})
    return j["choices"][0]["message"]["content"], u.get("prompt_tokens"), u.get("completion_tokens")

def load_prompt(version):
    return open(os.path.join("prompts", f"{version}.txt")).read()

def complete(system, user, ctx=None, step="draft"):
    """Returns dict(text, prompt_tokens, completion_tokens, cost_usd, latency_s, retries, provider)."""
    if not breaker.allow():
        metrics.LLM_CALLS.labels("breaker_open").inc(); raise BreakerOpen("circuit breaker open")
    last = None; retries = 0
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        t0 = time.time()
        try:
            if settings.LLM_PROVIDER == "openai_compat":
                text, pt, ct = _compat(system, user, ctx)
            else:
                text, pt, ct = _offline(system, user, ctx), None, None
            pt = pt if pt is not None else est_tokens(system + user)
            ct = ct if ct is not None else est_tokens(text)
            cost = pt / 1000 * settings.LLM_PRICE_PROMPT_PER_1K + ct / 1000 * settings.LLM_PRICE_COMPLETION_PER_1K
            breaker.ok(); metrics.LLM_CALLS.labels("ok").inc()
            metrics.TOKENS.labels("prompt", step).inc(pt); metrics.TOKENS.labels("completion", step).inc(ct); metrics.COST.labels(step).inc(cost)
            return {"text": text, "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": cost,
                    "latency_s": time.time() - t0, "retries": retries, "provider": settings.LLM_PROVIDER}
        except Exception as e:
            last = e; breaker.fail(); metrics.LLM_CALLS.labels("error").inc()
            if attempt < settings.LLM_MAX_RETRIES and breaker.allow():
                retries += 1; metrics.LLM_RETRIES.inc()
                time.sleep(min(2 ** attempt * 0.2, 2.0) + random.uniform(0, 0.1))
            else:
                break
    raise LLMError(f"model call failed after {retries} retries: {last}")
