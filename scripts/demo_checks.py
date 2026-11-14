"""Live checks for demonstration items 4-8 against a running API (offline provider). Usage: python scripts/demo_checks.py [base_url]"""
import json, sys, time, datetime as dt, httpx
B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
def alert(**kw):
    d = {"alert_id": f"demo-{int(time.time()*1000)%10**8}", "source": "synthetic", "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(), "pg_version": 16, "host_role": "primary",
         "metric": "disk_usage_pct", "value": 88 + (time.time() % 7) / 10, "threshold": 80, "oncall_note": "Disk filling on the primary, table and index sizes are not growing, cause unknown.", "labels": {"relation_sizes_growing": False}}
    d.update(kw); return d
def run(body, client="demo"):
    r = httpx.post(B + "/v1/runs", json=body, headers={"X-Client-Id": client})
    if r.status_code != 202: return r.status_code, r.json()
    rid = r.json()["run_id"]
    while True:
        j = httpx.get(f"{B}/v1/runs/{rid}/trace").json()
        if j["status"] in ("succeeded", "escalated", "failed"): return 202, j
        time.sleep(0.15)
def head(t): print(f"\n=== {t}")

head("Item 6: self-verification catches an unsupported answer, then searches again")
code, j = run(alert(labels={"relation_sizes_growing": False, "demo_fault": "unsupported_citation"}, alert_id="demo-unsupported-01", value=89.123))
v = [t for t in j["trace"] if t["name"] == "verifier_agent"]
print(" verifier calls:", [(t["ok"], [f["check"] for f in t["failures"]]) for t in v])
print(" retry decision:", [t["output"] for t in j["trace"] if t["name"] == "retry_with_new_strategy"])
print(" final:", j["status"], "| verified:", j["result"]["verification"]["ok"], "| attempts:", j["result"]["verification"]["attempts"], "| bogus citation in output:", any("99.9" in json.dumps(s) for s in j["result"]["steps"]))

head("Item 8: circuit breaker opens when the provider is unavailable; degradation path")
print(" provider down ->", httpx.post(B + "/admin/fault/llm", json={"down": True}).json())
for i in range(3):
    c, j = run(alert(alert_id=f"demo-brk-{i}", value=90.1 + i))
    llm = [t for t in j["trace"] if t["name"] in ("draft_summary", "extractive_fallback")]
    print(f" run {i}: status={j['status']} degraded={j['result']['degraded']} verified={j['result']['verification']['ok']} breaker={httpx.get(B+'/v1/stats').json()['breaker']} trace={[ (t['name'], (t.get('error') or '')[:60]) for t in llm]}")
print(" provider restored ->", httpx.post(B + "/admin/fault/llm", json={"down": False}).json(), "(breaker half-opens after BREAKER_RESET_S)")

head("Rate limiting per client (cost control)")
codes = [httpx.post(B + "/v1/runs", json=alert(alert_id=f"rl-{i}", value=91 + i / 100), headers={"X-Client-Id": "looping-client"}).status_code for i in range(45)]
print(" 45 rapid submissions from one client ->", {c: codes.count(c) for c in sorted(set(codes))}, "| other client still ok:", httpx.post(B + "/v1/runs", json=alert(alert_id="rl-other", value=92.5), headers={"X-Client-Id": "someone-else"}).status_code)
