"""Symptom -> hypothesis map. This is what inverts the retrieval direction (section 3): alerts arrive as
symptoms, documentation is organised by mechanism. Each hypothesis states the mechanism-language queries
to run and the keyword groups the cited passages must contain for the hypothesis to count as supported."""
import re

CAUSE_CLASSES = ["saturation", "contention", "configuration", "dependency_failure", "data_volume"]

def _has(t, *words): return any(re.search(w, t) for w in words)

HYPOTHESES = [
 {"id": "wal_slot", "cause_class": "saturation", "label": "WAL retained by an inactive replication slot",
  "queries": ["replication slot retains WAL when consumer disconnected pg_wal grows disk full",
              "inactive replication slot restart_lsn pg_replication_slots"],
  "must_have": [["slot", "slots"], ["wal"], ["restart_lsn", "active"]],
  "prior": lambda f: (0.15 + (0.6 if f["sizes_flat"] else 0) + (0.1 if f["host_role"] == "primary" else -0.3)
                      + (0.15 if _has(f["note"], r"slot|subscriber|consumer|standby (is )?(also )?down|replica (is )?(also )?down") else 0)
                      - (0.5 if _has(f["note"], r"archiv|backup|s3|temp file|temp_file|spill|\bsorts?\b") else 0))
                     if f["metric"] in ("disk_usage_pct", "wal_dir_gb") else 0.0},
 {"id": "archive_fail", "cause_class": "dependency_failure", "label": "WAL accumulating because archive_command is failing",
  "queries": ["archive_command failing WAL segments accumulate pg_wal", "pg_stat_archiver failed_count"],
  "must_have": [["archive_command", "archiving"], ["wal"], ["pg_stat_archiver", "failed"]],
  "prior": lambda f: (0.15 + (0.25 if f["sizes_flat"] else 0) + (0.6 if _has(f["note"], r"archiv|backup|s3|bucket|wal-g|pgbackrest") else 0))
                     if f["metric"] in ("disk_usage_pct", "wal_dir_gb") else 0.0},
 {"id": "temp_files", "cause_class": "saturation", "label": "Temporary files from large sorts or hashes",
  "queries": ["temporary files work_mem spill disk large query temp_file_limit"],
  "must_have": [["temporary", "temp"], ["work_mem"], ["disk"]],
  "prior": lambda f: (0.1 + (0.2 if f["sizes_flat"] else 0) + (0.6 if _has(f["note"], r"temp file|temp_file|temporary|\bsorts?\b|spill|large query") else 0))
                     if f["metric"] in ("disk_usage_pct",) else 0.0},
 {"id": "bloat", "cause_class": "configuration", "label": "Table and index bloat because autovacuum cannot keep up",
  "queries": ["autovacuum dead tuples bloat tables indexes grow vacuum", "long running transactions prevent vacuum"],
  "must_have": [["vacuum", "autovacuum"], ["dead"], ["bloat", "grow"]],
  "prior": lambda f: (0.1 + (0.55 if f["sizes_growing"] else 0) + (0.25 if _has(f["note"], r"bloat|vacuum|dead tuple|update|delete") else 0))
                     if f["metric"] == "disk_usage_pct" else 0.0},
 {"id": "data_growth", "cause_class": "data_volume", "label": "Unbounded data growth in application tables",
  "queries": ["table growth partitioning retention drop old data", "database object size functions pg_total_relation_size"],
  "must_have": [["partition", "partitioning"], ["growth", "grow", "large"], ["size", "sizes"]],
  "prior": lambda f: (0.1 + (0.45 if f["sizes_growing"] else 0) + (0.35 if _has(f["note"], r"growth|ingest|import|load|retention|new customer|backfill") else 0))
                     if f["metric"] in ("disk_usage_pct",) else 0.0},
 {"id": "lag_replay", "cause_class": "contention", "label": "Standby replay delayed by conflicting queries or I/O",
  "queries": ["replay_lag standby conflicting queries max_standby_streaming_delay", "replica lagging behind primary replay slow"],
  "must_have": [["replay_lag", "replay"], ["standby"], ["conflict", "conflicting", "pg_stat_replication"]],
  "prior": lambda f: (0.45 + (0.3 if _has(f["note"], r"quer|report|long|conflict|\bio\b|i/o|disk") else 0))
                     if f["metric"] == "replication_lag_s" else 0.0},
 {"id": "lag_network", "cause_class": "dependency_failure", "label": "Replication delayed by network path between nodes",
  "queries": ["write_lag network between primary and standby sending WAL", "replica lagging behind primary replay slow"],
  "must_have": [["write_lag", "network"], ["standby"], ["pg_stat_replication", "wal"]],
  "prior": lambda f: (0.2 + (0.6 if _has(f["note"], r"network|packet|dns|az |zone|vpn|link|firewall") else 0))
                     if f["metric"] == "replication_lag_s" else 0.0},
 {"id": "locks", "cause_class": "contention", "label": "Sessions blocked by locks or idle-in-transaction holders",
  "queries": ["explicit locking blocking sessions pg_blocking_pids idle in transaction", "pg_stat_activity wait_event idle in transaction"],
  "must_have": [["lock", "locks", "locking"], ["pg_stat_activity", "pg_blocking_pids", "pg_locks"], ["transaction"]],
  "prior": lambda f: (0.2 + (0.55 if _has(f["note"], r"lock|block|waiting|idle in transaction|deadlock|stuck") else 0))
                     if f["metric"] in ("latency_p99_ms", "error_rate_pct", "availability_pct") else 0.0},
 {"id": "connections", "cause_class": "saturation", "label": "Connection exhaustion against max_connections",
  "queries": ["max_connections no more connections available connection pooler", "pg_stat_activity connections count"],
  "must_have": [["max_connections"], ["connection", "connections"], ["pooler", "backends", "pg_stat_activity"]],
  "prior": lambda f: (0.25 + (0.55 if f["metric"] == "connections_pct" else 0) + (0.4 if _has(f["note"], r"too many connections|max_connections|pool|connection") else 0))
                     if f["metric"] in ("connections_pct", "error_rate_pct", "latency_p99_ms", "availability_pct") else 0.0},
 {"id": "io_sat", "cause_class": "saturation", "label": "Storage I/O saturation from checkpoints or write bursts",
  "queries": ["checkpoints I/O saturation commit latency pg_stat_io bgwriter", "write burst max_wal_size checkpoint"],
  "must_have": [["checkpoint", "checkpoints"], ["i/o", "io", "pg_stat_io"], ["latency", "write"]],
  "prior": lambda f: (0.2 + (0.5 if _has(f["note"], r"\bio\b|i/o|iowait|checkpoint|write burst|fsync|disk latency") else 0))
                     if f["metric"] in ("latency_p99_ms", "cpu_pct") else 0.0},
 {"id": "slow_query", "cause_class": "configuration", "label": "Slow statements found via query logging",
  "queries": ["log_min_duration_statement slow statements auto_explain latency"],
  "must_have": [["log_min_duration_statement"], ["slow", "latency"], ["statement", "statements"]],
  "prior": lambda f: (0.2 + (0.55 if _has(f["note"], r"slow quer|query|plan|index|regress|deploy") else 0))
                     if f["metric"] in ("latency_p99_ms", "cpu_pct") else 0.0},
 {"id": "external_dep", "cause_class": "dependency_failure", "label": "External dependency failure (volume, DNS, network, backup target)",
  "queries": ["external dependency failures operating system logs mounted volumes DNS network fault"],
  "must_have": [["dependency", "dependencies"], ["volume", "network", "dns"], ["logs", "status"]],
  "prior": lambda f: (0.2 + (0.6 if _has(f["note"], r"dns|network (fault|partition|outage)|unreachable|volume (failed|fault|lost)|mounted volume|ebs|nfs|dependency|upstream") else 0))
                     if f["metric"] in ("error_rate_pct", "availability_pct", "latency_p99_ms") else 0.0},
]
BY_ID = {h["id"]: h for h in HYPOTHESES}

def features(req, note_clean: str):
    labels = {k.lower(): v for k, v in (req.labels or {}).items()}
    def flag(*names):
        for n in names:
            if n in labels: return bool(labels[n]) if not isinstance(labels[n], str) else labels[n].lower() in ("true", "1", "yes")
        return None
    growing = flag("relation_sizes_growing", "table_sizes_growing", "sizes_growing")
    note = (note_clean or "").lower()
    if growing is None:
        if re.search(r"(table|index|relation)s?( and (index|table)s?)? sizes? (are )?(not growing|flat|stable|unchanged)", note): growing = False
        elif re.search(r"(table|index|relation)s?.{0,30}(growing|bloat|large)", note): growing = True
    return {"metric": req.metric, "host_role": req.host_role, "note": note, "value": req.value, "threshold": req.threshold,
            "pg_version": req.pg_version, "sizes_growing": growing is True, "sizes_flat": growing is False, "labels": labels}

def rank(f):
    out = [{"id": h["id"], "cause_class": h["cause_class"], "label": h["label"], "prior": round(max(0.0, min(1.0, h["prior"](f))), 3)} for h in HYPOTHESES]
    return sorted([o for o in out if o["prior"] > 0], key=lambda x: -x["prior"])

def coverage(h, texts):
    blob = " ".join(texts).lower()
    groups = h["must_have"]
    hit = [any(w in blob for w in g) for g in groups]
    return sum(hit) / len(groups), hit
