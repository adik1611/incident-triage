"""Submit each UI example to the running API and print the outcome. Usage: python scripts/smoke.py [base_url]"""
import json, sys, time, datetime as dt, httpx
base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
for e in json.load(open("eval/ui_examples.json")):
    body = dict(e["request"])
    if body["fired_at"] == "__NOW__": body["fired_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    t0 = time.time(); r = httpx.post(base + "/v1/runs", json=body, headers={"X-Client-Id": "smoke"})
    if r.status_code != 202:
        print(f"\n## {e['name']}\n  HTTP {r.status_code}", r.json().get("errors") or r.json()); continue
    rid = r.json()["run_id"]
    while True:
        run = httpx.get(f"{base}/v1/runs/{rid}").json()
        if run["status"] in ("succeeded", "escalated", "failed"): break
        time.sleep(0.2)
    res = run["result"]
    print(f"\n## {e['name']}\n  {run['status']} in {time.time()-t0:.2f}s | {res.get('severity')} -> {res.get('route')} | {res.get('basis')}")
    if res.get("status") == "answered": print("  cause:", res["proposed_cause_class"], "-", res["hypothesis"], "| cites:", [c["id"] for c in res["citations"]], "| guardrails:", res["guardrails"])
    else: print("  escalation:", res.get("escalation", {}).get("reason"), "| guardrails:", res.get("guardrails"))
