---
doc: Runbook RB-001
version: 16
chapter: Runbook
---

## [RB-001] Disk filling on primary with flat relation sizes (WAL accumulation)
Symptoms: disk usage on the primary climbs steadily; table and index sizes are not growing; pg_wal directory is large; alert says disk filling, cause unknown; on-call sees no obvious heavy query.
Cause class: saturation (WAL retained by replication slot or failing archive_command).
Confirmation: (1) du on pg_wal shows it is the growing directory. (2) SELECT slot_name, active, restart_lsn, wal_status FROM pg_replication_slots; look for active = false. (3) SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) per slot. (4) SELECT failed_count, last_failed_time FROM pg_stat_archiver. See PG16-26.2.6, PG16-27.2.4, PG16-26.3.1.
First response (proposal only, on-call decides): identify the owner of each inactive slot; if the consumer is gone and unrecoverable, drop it with pg_drop_replication_slot after approval; if the consumer is recoverable, restart it so it advances. Then set max_slot_wal_keep_size (PG16-19.6.1) so a future stalled consumer cannot fill the disk. If pg_stat_archiver shows failures, fix archive_command destination.
Escalate: page secondary if free disk is under 20 percent; incident commander if under 10 percent or time to full is under 60 minutes.
