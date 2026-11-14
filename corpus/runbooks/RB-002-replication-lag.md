---
doc: Runbook RB-002
version: 16
chapter: Runbook
---

## [RB-002] Replica lagging behind primary
Symptoms: replication lag alert on a standby; stale reads; replay delay grows; primary looks healthy.
Cause class: contention or saturation on the standby, or dependency failure on the network path.
Confirmation: (1) On the primary SELECT application_name, write_lag, flush_lag, replay_lag FROM pg_stat_replication. (2) If replay_lag dominates, check pg_stat_database_conflicts and long queries on the standby. (3) If write_lag dominates, check network between nodes. See PG16-27.2.5, PG16-26.2.3.
First response (proposal only): cancel or reschedule long standby queries; review max_standby_streaming_delay; check standby I/O saturation; for network cases engage the infrastructure team.
Escalate: page secondary if lag exceeds 300 seconds for 10 minutes; incident commander if lag exceeds 1800 seconds or the replica serves failover.
