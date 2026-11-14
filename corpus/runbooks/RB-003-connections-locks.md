---
doc: Runbook RB-003
version: 16
chapter: Runbook
---

## [RB-003] Latency spike or errors from blocked sessions and connection exhaustion
Symptoms: latency p99 climbs; error rate rises; connection count near max_connections; CPU low; many sessions waiting; idle in transaction sessions.
Cause class: contention (locks) or saturation (connections).
Confirmation: (1) SELECT state, wait_event_type, count(*) FROM pg_stat_activity GROUP BY 1,2. (2) Use pg_blocking_pids to find the root blocker and its xact_start. (3) Compare current connections to max_connections. See PG16-13.3, PG16-28.2, PG16-19.3.1.
First response (proposal only): identify and, with approval, terminate the root blocker with pg_terminate_backend; introduce or tune a connection pooler; set idle_in_transaction_session_timeout to stop recurrence.
Escalate: page secondary if error rate exceeds 5 percent for 5 minutes; incident commander if the service is unavailable.
