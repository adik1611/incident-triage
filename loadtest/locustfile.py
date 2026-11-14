"""Locust load test (D6). Measures a COMPLETE run: submit -> poll until terminal. Run with the LLM stub provider.
  locust -f loadtest/locustfile.py --headless -u 20 -r 5 -t 60s --host http://localhost:8000
The custom 'run_total' request type is the end-to-end latency the SLO is judged on."""
import os, time, random, datetime as dt
import gevent
from locust import HttpUser, task, constant_throughput, events

NOTES = ["Disk usage on the primary is climbing steadily. Table and index sizes are not growing. On-call note: disk filling, cause unknown.",
         "Replica lag growing since the reporting job started; long queries running on the standby.",
         "Many sessions blocked, idle in transaction holder, lock waits piling up, cpu low."]

def payload(i):
    n = i % 3
    if n == 0: m, role, labels = "disk_usage_pct", "primary", {"relation_sizes_growing": False}
    elif n == 1: m, role, labels = "replication_lag_s", "replica", {}
    else: m, role, labels = "latency_p99_ms", "primary", {}
    FIXED = os.getenv("LOAD_FIXED") == "1"   # repeated identical alerts (alert-storm shape) so the run cache can be measured
    val = {"disk_usage_pct": 85 + random.random() * 10, "replication_lag_s": 400 + random.random() * 500, "latency_p99_ms": 6000 + random.random() * 1000}[m]
    if FIXED: val = {"disk_usage_pct": 88, "replication_lag_s": 420, "latency_p99_ms": 6200}[m]
    thr = {"disk_usage_pct": 80, "replication_lag_s": 300, "latency_p99_ms": 2000}[m]
    return {"alert_id": f"load-{random.randint(0, 10**9)}", "source": "synthetic", "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(), "pg_version": 16,
            "host_role": role, "metric": m, "value": round(val, 2) if not FIXED else val, "threshold": thr, "oncall_note": NOTES[n], "labels": labels}

PENDING = []

def _await_result(client, rid, t0):
    status = None
    while time.time() - t0 < 180:
        r = client.get(f"/v1/runs/{rid}", name="GET /v1/runs/{id} (poll)")
        status = r.json().get("status") if r.status_code == 200 else None
        if status in ("succeeded", "escalated", "failed"): break
        gevent.sleep(0.25)
    ok = status in ("succeeded", "escalated")
    events.request.fire(request_type="RUN", name="run_total (submit->result)", response_time=(time.time() - t0) * 1000, response_length=0,
                        exception=None if ok else Exception(f"run ended {status}"), context={})

class Oncall(HttpUser):
    """OPEN-LOOP arrivals: each user submits 1 run/s whether or not earlier runs have finished, so users == offered runs/s
    (a closed-loop user that waits for its result caps concurrency and hides overload)."""
    wait_time = constant_throughput(1)
    @task
    def submit(self):
        t0 = time.time()
        h = {"X-Client-Id": f"load-{random.randint(0, 10**6)}"}
        with self.client.post("/v1/runs", json=payload(random.randint(0, 99)), headers=h, name="POST /v1/runs (submit only)", catch_response=True) as r:
            if r.status_code != 202: r.failure(f"submit {r.status_code}"); return
            rid = r.json()["run_id"]
        PENDING.append(gevent.spawn(_await_result, self.client, rid, t0))

@events.test_stop.add_listener
def _drain(environment, **kw):
    gevent.joinall(PENDING, timeout=240)   # count runs still queued at the end, so overload is not hidden by survivorship
