---
doc: PostgreSQL 16 Documentation (paraphrased study notes)
version: 16
chapter: Replication and Write-Ahead Log
---

## [PG16-26.2.6] Replication Slots
Replication slots are a server-side bookmark of how far a consumer (a physical standby or a logical decoding client) has read. The primary will not remove write-ahead log (WAL) segments that a slot still needs, even when the consumer is disconnected, crashed, or has been decommissioned. A slot whose consumer has stopped therefore makes pg_wal on the primary grow without limit until the disk is full. Slots are visible in the view pg_replication_slots; the columns active, restart_lsn and wal_status show whether a slot is connected and how much WAL it is holding back. See also PG16-19.6.1 (max_slot_wal_keep_size) and PG16-27.2.4 (pg_replication_slots monitoring). Remove a slot that is no longer needed with pg_drop_replication_slot.

## [PG16-19.6.1] max_slot_wal_keep_size
This setting caps how much WAL, in megabytes, the primary keeps for replication slots. The default of -1 means no limit, so an abandoned slot can fill the disk. When a limit is set and a slot falls further behind than the limit, the slot is invalidated: its wal_status becomes lost and the consumer can no longer resume from that slot. This modifies the behaviour described in PG16-26.2.6, because a bounded setting converts a disk-full outage into a slot loss.

## [PG16-27.2.4] Monitoring replication slots
Query pg_replication_slots to list slot_name, slot_type, active, restart_lsn, confirmed_flush_lsn and wal_status. In PostgreSQL 16 a slot with active = false and an old restart_lsn is the signature of a stopped consumer. The size of WAL retained for a slot can be estimated with pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn). Note that the column inactive_since is not available in version 16; it was introduced in a later major version, so on 16 use active and restart_lsn instead.

## [PG16-26.2.5] Streaming replication and standby feedback
A standby can send feedback to the primary. With hot_standby_feedback enabled, the primary delays cleanup of rows that standby queries still need, which can increase bloat on the primary. Streaming replication state of connected standbys is exposed in pg_stat_replication, including write_lag, flush_lag and replay_lag.

## [PG16-27.2.5] Replication lag
Lag has three stages: sending, flushing and replaying WAL on the standby. Large replay_lag with small write_lag and flush_lag means the standby receives WAL but is slow to apply it, commonly because of conflicting standby queries, I/O saturation on the standby, or long single transactions. Large write_lag points to the network or the primary sender. Use pg_stat_replication on the primary and pg_last_xact_replay_timestamp on the standby.

## [PG16-26.2.3] Standby query conflicts
Queries on a hot standby can conflict with WAL replay. Replay waits up to max_standby_streaming_delay before cancelling the conflicting query. A large value keeps queries alive but lets replay lag grow. pg_stat_database_conflicts counts cancellations by cause.

## [PG16-26.3.1] Continuous archiving and archive_command
When archive_mode is on, a WAL segment is kept in pg_wal until archive_command reports success. If archive_command keeps failing, for example because the destination is full or unreachable, completed segments accumulate in pg_wal and the disk fills even though no slot is involved. The view pg_stat_archiver shows failed_count and last_failed_time.

## [PG16-19.5.1] wal_keep_size and max_wal_size
wal_keep_size keeps a fixed amount of extra WAL for standbys that stream without a slot. max_wal_size is a soft limit that triggers checkpoints; heavy write bursts can exceed it temporarily. Neither setting retains WAL for a slot beyond what PG16-26.2.6 describes.

## [PG16-30.5] Logical decoding and logical slots
A logical replication slot retains WAL until the subscriber confirms receipt. A stalled subscriber or a subscription that was dropped without dropping its slot on the publisher causes the same WAL retention as a stalled physical slot. Logical slots also hold back the catalog xmin, which can delay vacuum of system catalogs.
