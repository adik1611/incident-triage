"""Prometheus metrics (D5, A6, D2, L5)."""
import time
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY, generate_latest, CONTENT_TYPE_LATEST

B = (0.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 20, 30, 60, 120)
START = time.time()
RUNS = Counter("triage_runs_total", "Completed runs", ["status", "route"])
RUN_SEC = Histogram("triage_run_seconds", "End-to-end run latency (submit->result)", buckets=B)
STEP_SEC = Histogram("triage_step_seconds", "Per-step latency", ["step"], buckets=B)
TOOL_CALLS = Counter("triage_tool_calls_total", "Tool calls", ["tool"])
STEPS_PER_RUN = Histogram("triage_steps_per_run", "Graph steps per run", buckets=(1, 2, 3, 4, 5, 6, 8, 10, 15))
TOOLS_PER_RUN = Histogram("triage_tool_calls_per_run", "Tool calls per run", buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10))
LLM_RETRIES = Counter("triage_llm_retries_total", "LLM call retries")
LLM_CALLS = Counter("triage_llm_calls_total", "LLM calls", ["outcome"])
BREAKER = Gauge("triage_breaker_open", "1 if LLM circuit breaker open")
TOKENS = Counter("triage_tokens_total", "Tokens", ["kind", "step"])
COST = Counter("triage_cost_usd_total", "Cost in USD", ["step"])
HTTP = Counter("triage_http_requests_total", "HTTP requests", ["path", "code"])
HTTP_SEC = Histogram("triage_http_seconds", "HTTP handler latency", ["path"], buckets=B)
QUARANTINED = Counter("triage_quarantined_total", "Rejected requests quarantined")
GUARD = Counter("triage_guardrail_total", "Guardrail activations", ["kind"])
CACHE = Counter("triage_cache_total", "Cache lookups", ["layer", "result"])
RATELIM = Counter("triage_rate_limited_total", "Requests rejected by rate limit")
FEEDBACK = Counter("triage_feedback_total", "Feedback", ["rating"])
VERIFY = Counter("triage_verification_total", "Verifier outcomes", ["outcome"])
INDEX_CHUNKS = Gauge("triage_index_chunks", "Chunks in index")
INDEX_BUILT = Gauge("triage_index_built_timestamp", "Unix time index was built")
INDEX_RATE = Gauge("triage_index_chunks_per_second", "Index build throughput")
DEGRADED = Counter("triage_degraded_runs_total", "Runs that fell back to the extractive path because the model was unavailable")
UP = Gauge("triage_uptime_seconds", "Process uptime")

def render():
    UP.set(time.time() - START)
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
