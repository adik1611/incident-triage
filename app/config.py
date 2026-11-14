"""All environment-specific configuration comes from environment variables (D4)."""
import os

def _f(name, default): return float(os.getenv(name, default))
def _i(name, default): return int(os.getenv(name, default))
def _s(name, default=""): return os.getenv(name, default)

class Settings:
    # corpus / index
    CORPUS_DIR = _s("CORPUS_DIR", "corpus")
    INDEX_DIR = _s("INDEX_DIR", "data/index")
    QUARANTINE_FILE = _s("QUARANTINE_FILE", "data/quarantine.jsonl")
    COVERED_PG_VERSION = _i("COVERED_PG_VERSION", 16)
    # queue / state
    QUEUE_BACKEND = _s("QUEUE_BACKEND", "memory")      # memory | redis
    REDIS_URL = _s("REDIS_URL", "redis://redis:6379/0")
    LOCAL_WORKERS = _i("LOCAL_WORKERS", 2)             # in-process workers when backend=memory
    # model provider (placed behind an interface; swap by config)
    LLM_PROVIDER = _s("LLM_PROVIDER", "offline")       # offline | openai_compat
    LLM_BASE_URL = _s("LLM_BASE_URL", "")
    LLM_API_KEY = _s("LLM_API_KEY", "")
    LLM_MODEL = _s("LLM_MODEL", "")
    LLM_TIMEOUT_S = _f("LLM_TIMEOUT_S", 20)
    LLM_MAX_RETRIES = _i("LLM_MAX_RETRIES", 2)
    LLM_PRICE_PROMPT_PER_1K = _f("LLM_PRICE_PROMPT_PER_1K", 0.0)
    LLM_PRICE_COMPLETION_PER_1K = _f("LLM_PRICE_COMPLETION_PER_1K", 0.0)
    BREAKER_FAILS = _i("BREAKER_FAILS", 3)
    BREAKER_RESET_S = _f("BREAKER_RESET_S", 20)
    # agent loop bounds (A1)
    MAX_STEPS = _i("MAX_STEPS", 10)
    MAX_TOOL_CALLS = _i("MAX_TOOL_CALLS", 8)
    MAX_RUN_SECONDS = _f("MAX_RUN_SECONDS", 60)
    MAX_VERIFY_RETRIES = _i("MAX_VERIFY_RETRIES", 2)
    TOOL_TIMEOUT_S = _f("TOOL_TIMEOUT_S", 5)
    AGENT_MODE = _s("AGENT_MODE", "agentic")           # agentic | single_pass (baseline for eval)
    PROMPT_VERSION = _s("PROMPT_VERSION", "draft_v2")
    MIN_RETRIEVAL_SCORE = _f("MIN_RETRIEVAL_SCORE", 0.12)
    AMBIGUITY_MARGIN = _f("AMBIGUITY_MARGIN", 0.25)
    # verifier agent as a separate service (empty = in-process fallback)
    VERIFIER_URL = _s("VERIFIER_URL", "")
    # cache / rate limit (D7)
    CACHE_RUNS = _s("CACHE_RUNS", "1") == "1"
    CACHE_TTL_S = _i("CACHE_TTL_S", 300)
    RATE_LIMIT_PER_MIN = _i("RATE_LIMIT_PER_MIN", 30)
    # observability
    OTEL_ENDPOINT = _s("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    SERVICE_NAME = _s("SERVICE_NAME", "triage")
    WORKER_METRICS_PORT = _i("WORKER_METRICS_PORT", 9100)
    # stub (D6)
    STUB_DELAY_MS = _i("STUB_DELAY_MS", 800)
    DEMO_MODE = _s("DEMO_MODE", "1") == "1"       # enables fault-injection knobs for the demonstration only

settings = Settings()
