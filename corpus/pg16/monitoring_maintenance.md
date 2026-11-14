---
doc: PostgreSQL 16 Documentation (paraphrased study notes)
version: 16
chapter: Monitoring, Routine Maintenance, Locking, Resources
---

## [PG16-24.1] Routine vacuuming and table bloat
Updated and deleted rows leave dead tuples that VACUUM reclaims. If vacuum cannot keep pace, tables and indexes grow (bloat) and disk usage rises together with relation sizes. Long-running transactions and abandoned prepared transactions prevent vacuum from removing dead rows. Use pg_stat_user_tables.n_dead_tup and last_autovacuum to check.

## [PG16-24.1.6] Autovacuum daemon
Autovacuum launches workers automatically. Its throttling parameters (autovacuum_vacuum_cost_limit, autovacuum_naptime, scale factors) may be too conservative for large tables, leaving bloat to accumulate. This is a configuration cause: sizes grow because maintenance is under-provisioned.

## [PG16-28.2] Statistics views and pg_stat_activity
pg_stat_activity shows one row per backend with state, wait_event_type, wait_event, xact_start and query. States such as idle in transaction identify sessions that hold locks or snapshots while doing nothing.

## [PG16-13.3] Explicit locking and blocking
Row-level and table-level locks make sessions wait on each other. A session that holds a lock inside a long or idle transaction blocks others, raising latency and queue length without raising CPU. pg_locks combined with pg_stat_activity identifies blockers; pg_blocking_pids(pid) returns the blocking process ids.

## [PG16-13.3.4] Deadlocks
Two sessions that wait on each other form a deadlock. The server detects it after deadlock_timeout and aborts one transaction, which appears as an error spike. Consistent lock ordering in applications prevents most deadlocks.

## [PG16-19.3.1] max_connections
max_connections limits concurrent backends. When it is reached, new clients receive an error that no more connections are available. Each connection costs memory, so raising the limit is not free; a connection pooler is the usual remedy for many short-lived clients. Connection saturation is a resource saturation cause.

## [PG16-19.4.1] work_mem and temporary files
Sorts and hashes that exceed work_mem spill to temporary files. Large queries can create very large temporary files that fill disk until the query finishes. log_temp_files records them and temp_file_limit caps the space per session. Temporary files are removed when the query ends, so this pattern correlates with specific running queries and disk usage falls again afterwards.

## [PG16-27.1.5] Checkpoints and I/O saturation
Frequent checkpoints or heavy write bursts can saturate storage I/O, raising commit latency. pg_stat_bgwriter and pg_stat_io (new in 16) show where writes originate.

## [PG16-20.7.1] Query logging for latency investigation
log_min_duration_statement records statements slower than a threshold. auto_explain captures plans of slow statements. Comparing the slow statement list before and after the alert time narrows a latency spike to a query or to a lock wait.

## [PG16-5.11] Table partitioning
Partitioning splits a large table into smaller pieces so old data can be dropped cheaply. Unbounded growth of a single table is a data-volume cause that partitioning and retention policies address.

## [PG16-9.27.5] Database object size functions
pg_total_relation_size, pg_relation_size and pg_database_size report sizes. Comparing them across time shows whether relations are growing. When disk usage rises while all relation sizes are flat, the space is being consumed outside the relations, for example by WAL, temporary files or logs.

## [PG16-29.1] External dependency failures
Application errors reported by the database tier can originate from dependencies such as a failed disk volume, a DNS or network fault between nodes, or a failing backup target. Check operating system logs and the status of mounted volumes before changing PostgreSQL settings.
