"""Data contract + ingestion-boundary validation (F1, F2, D1). Rejects are quarantined."""
import json, os, time, datetime as dt
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ValidationError, model_validator
from .config import settings

PCT_METRICS = {"disk_usage_pct", "connections_pct", "error_rate_pct", "cpu_pct"}
METRICS = PCT_METRICS | {"replication_lag_s", "latency_p99_ms", "wal_dir_gb", "availability_pct"}

class AlertRequest(BaseModel):
    alert_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    source: Literal["prometheus", "pagerduty", "cloudwatch", "synthetic", "replay"]
    fired_at: dt.datetime
    pg_version: int = Field(ge=9, le=20)
    host_role: Literal["primary", "replica"]
    metric: Literal[tuple(sorted(METRICS))]  # type: ignore
    value: float = Field(ge=0)
    threshold: float = Field(ge=0)
    oncall_note: str = Field(default="", max_length=1000)
    labels: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def cross_field(self):
        if self.metric in PCT_METRICS and self.value > 100:
            raise ValueError("percentage metric value must be <= 100")
        if self.metric in PCT_METRICS and self.threshold > 100:
            raise ValueError("percentage metric threshold must be <= 100")
        if self.metric == "replication_lag_s" and self.host_role != "replica":
            raise ValueError("replication_lag_s alerts must come from a replica")
        if self.metric != "availability_pct" and self.value < self.threshold:
            raise ValueError("alert not breaching: value is below threshold")
        now = dt.datetime.now(dt.timezone.utc)
        t = self.fired_at if self.fired_at.tzinfo else self.fired_at.replace(tzinfo=dt.timezone.utc)
        if t > now + dt.timedelta(minutes=5):
            raise ValueError("fired_at is in the future")
        if (now - t) > dt.timedelta(days=30):
            raise ValueError("fired_at older than 30 days: replay with source=replay not supported")
        for k, v in self.labels.items():
            if not isinstance(v, (str, int, float, bool)) or len(str(k)) > 40 or len(str(v)) > 120:
                raise ValueError(f"label '{k}' must be a short scalar")
        return self

def field_errors(e: ValidationError):
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(request)"
        out.append({"field": loc, "error": err["msg"], "type": err["type"]})
    return out

def quarantine(payload: Any, errors, correlation_id: str):
    os.makedirs(os.path.dirname(settings.QUARANTINE_FILE) or ".", exist_ok=True)
    rec = {"ts": time.time(), "correlation_id": correlation_id, "errors": errors,
           "payload": payload if isinstance(payload, (dict, list, str, int, float, type(None))) else str(payload)}
    with open(settings.QUARANTINE_FILE, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec

def validate(payload: Any, correlation_id: str):
    """Returns (AlertRequest|None, errors|None). Any failure is quarantined; no agent run is created."""
    try:
        return AlertRequest.model_validate(payload), None
    except ValidationError as e:
        errs = field_errors(e)
        quarantine(payload, errs, correlation_id)
        return None, errs
