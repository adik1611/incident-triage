"""Structured JSON logging with correlation ID (D5)."""
import json, logging, sys, contextvars, time
from .config import settings
corr_id = contextvars.ContextVar("corr_id", default="-")

class JsonFmt(logging.Formatter):
    def format(self, r):
        d = {"ts": round(time.time(), 3), "level": r.levelname, "service": settings.SERVICE_NAME,
             "correlation_id": corr_id.get(), "msg": r.getMessage()}
        d.update(getattr(r, "extra_fields", {}))
        return json.dumps(d, default=str)

def get_logger(name="triage"):
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout); h.setFormatter(JsonFmt()); lg.addHandler(h); lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg

def log(msg, **kw):
    get_logger().info(msg, extra={"extra_fields": kw})
