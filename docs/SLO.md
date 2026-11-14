# Service level objective (declared BEFORE any load measurement - D6)

Declared: 2026-10-30, before the first Locust run. Not edited afterwards (see git history / file timestamp).

**SLO: p95 of a complete run, measured from the moment `POST /v1/runs` is sent until the polled status becomes terminal, must be <= 10 s at a sustained 5 new runs/s, with < 1% failed runs. Model provider replaced by the stub (800 ms fixed delay).**

Justification (assignment section 1): the engineer is paged at 3 a.m. and must decide class, runbook, severity and escalation "quickly". A manual first look at `pg_replication_slots` or `pg_stat_activity` takes tens of seconds to a couple of minutes. A triage assistant is only useful if it returns while the engineer is still orienting; beyond ~10 s it competes with doing the lookup by hand. 5 runs/s is far above a realistic pager rate for one database fleet (a storm of alerts, not steady state), so it is a stress target, not a typical one.

Measured as: submission -> terminal status (not the 202 response alone). Tool: `loadtest/locustfile.py`.
