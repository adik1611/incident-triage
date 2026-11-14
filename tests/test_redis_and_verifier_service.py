"""Exercises the compose-only code paths without Docker: RedisStore + worker loop (fakeredis) and the verifier as a separate HTTP service."""
import os, sys, time, threading, datetime as dt, subprocess
sys.path.insert(0, os.getcwd())
os.environ.update({"QUEUE_BACKEND": "redis", "CACHE_RUNS": "0", "VERIFIER_URL": "http://localhost:8100"})
VER = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.verifier_service:app", "--port", "8100", "--log-level", "warning"]); time.sleep(3)
import fakeredis, redis
_srv = fakeredis.FakeServer()
redis.Redis.from_url = staticmethod(lambda *a, **k: fakeredis.FakeRedis(server=_srv, decode_responses=True))
from app import runs, contract
from app.config import settings
assert isinstance(runs.STORE, runs.RedisStore)

def submit(**kw):
    d = {"alert_id": "redis-test-1", "source": "synthetic", "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(), "pg_version": 16, "host_role": "primary",
         "metric": "disk_usage_pct", "value": 88, "threshold": 80, "oncall_note": "Disk filling, table and index sizes are not growing.", "labels": {"relation_sizes_growing": False}}
    d.update(kw); req, e = contract.validate(d, "t"); assert req, e
    run = runs.new_run(req, "cid", "t"); runs.STORE.put(run); runs.STORE.enqueue(run["run_id"]); return run["run_id"]

def wait(rid):
    for _ in range(100):
        r = runs.STORE.get(rid)
        if r["status"] in ("succeeded", "escalated", "failed"): return r
        time.sleep(0.1)
    raise AssertionError("timeout")

stop = threading.Event(); th = threading.Thread(target=runs.worker_loop, args=(stop,), daemon=True); th.start()
r = wait(submit())
print("redis queue + worker + HTTP verifier:", r["status"], r["result"]["hypothesis"], "| verified", r["result"]["verification"]["ok"])
assert r["status"] == "succeeded" and r["result"]["verification"]["ok"]
print("\nverifier service killed mid-flight ->", end=" ")
VER.kill(); VER.wait(); time.sleep(1)
r = wait(submit(alert_id="redis-test-2"))
print(r["status"], "| answer returned:", r["result"]["status"] == "answered", "| reason:", r["result"]["escalation"]["reason"], "| route:", r["result"]["route"])
assert r["status"] == "escalated" and r["result"]["status"] != "answered"
stop.set(); print("OK")
