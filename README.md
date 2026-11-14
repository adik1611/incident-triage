# Incident Triage & Runbook Assistant (MiniProject 08)

Takes a PostgreSQL alert + on-call note and **proposes** a cause class, cited first-response steps, severity and routing. It never executes remediation, and it declines (escalates) when evidence is ambiguous, missing, or the PG version is not covered.

## Run it (website)
```
pip install -r requirements.txt
python -m app.corpus                      # build index (idempotent, incremental)
uvicorn app.main:app --port 8000          # open http://localhost:8000
```
Full stack (Redis, workers, verifier service, Prometheus, Grafana, Jaeger): `cp .env.example .env && docker compose up --build --scale worker=3`.

## Architecture
- **API** (`app/main.py`): validates at the boundary (`app/contract.py`, rejects quarantined, no agent started), rate limits, run cache, async `POST /v1/runs` -> poll `GET /v1/runs/{id}` (+ `/trace`, `/feedback`, `/metrics`).
- **Investigator agent** (LangGraph, `app/agents.py`): classify symptom -> plan -> tool calls (`search_corpus`, `get_clause`, cross-reference following, derived re-query) -> draft. Bounded by steps, tool calls, wall clock.
- **Verifier/Router agent** (`app/verifier.py`, optional separate service): re-checks citations, quotes, version, step support, hypothesis coverage; assigns severity/route. Failure -> retry with a new strategy -> escalate. If the verifier is down, no unverified answer is returned.
- **Guardrails** (`app/guardrails.py`), **LLM layer** with retries + circuit breaker + extractive degradation (`app/llm.py`), **metrics** (`app/metrics.py`).
- **Corpus**: `corpus/` (PG16 notes, 3 runbooks, severity policy) -> chunk by passage id -> hashed embeddings -> FAISS.

## Results (22-scenario set, `python scripts/evaluate.py --compare-mode`)
| metric | single-pass | agentic |
|---|---|---|
| outcome accuracy | 0.409 | 0.955 |
| hypothesis accuracy | 0.235 | 0.941 |
| groundedness / faithfulness | 1.0 / 1.0 | 1.0 / 1.0 |
| hallucination rate | 0.0 | 0.0 |
| section 4.1 case | escalated | answered: WAL retained by inactive replication slot (cites PG16-26.2.6, RB-001, PG16-19.6.1, PG16-27.2.4) |

Prompt v1 vs v2: identical accuracy; v2 adds inline citations in summaries (summary_cited_rate 0 -> 1.0).

## Load test (stub model, 800 ms; SLO in `docs/SLO.md`: p95 <= 10 s at 5 runs/s)
Open-loop Locust, 2-core sandbox, in-process workers:
| workers | offered/s | p50 | p95 |
|---|---|---|---|
| 2 | 5 | 25 s | 46 s (backlog, SLO missed) |
| 8 | 5 | 1.2 s | 1.2 s (SLO met) |
| 8 | 10 | 4.4 s | 6.3 s |
| 8 | 20 | 30 s | 55 s (saturated ~8.5 runs/s) |
| 32 | 20 | 1.8 s | 3.0 s |
| 32 | 40 | 5.7 s | 11 s (CPU-bound, ~1 core) |
Run cache on repeated alerts @20/s: p50 0.07 s vs 31 s without. Bottleneck: worker concurrency (model latency) first, then Python CPU. Redis/worker/Docker paths are covered by `tests/test_redis_and_verifier_service.py` (fakeredis); the Docker daemon was unavailable in the build sandbox, so compose was validated for syntax only.

## Cost
Offline provider reports $0. With illustrative prices ($0.15/M prompt, $0.60/M completion): ~359 prompt + 39 completion tokens per answered run = ~$0.0075 per 100 runs; prompt tokens are ~69% of cost. Escalated runs make no model call.

## Tests
`python -m pytest tests/test_core.py` (18 pass); `python tests/test_redis_and_verifier_service.py`; `python scripts/demo_checks.py` (verifier catch + retry, circuit breaker, rate limit).

## Known limits
Offline provider is deterministic (not a real LLM); plug one in via `LLM_PROVIDER=openai_compat`. Embeddings are hashed n-grams, not a neural model. Corpus is small, paraphrased PG16 notes. Scenario set is hand-written by the same author as the system, so accuracy is optimistic. Docker stack not run end to end here.
