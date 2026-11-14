#!/usr/bin/env bash
# Usage: scripts/loadrun.sh <workers> <offered_rps> [duration]
# Starts the model stub + API (stub provider), runs Locust (offered RPS == users, 1 run/s each), prints end-to-end results.
W=${1:-2}; U=${2:-5}; D=${3:-40s}; C=${CACHE_RUNS:-0}
cd "$(dirname "$0")/.."; mkdir -p loadtest/results data
STUB_DELAY_MS=${STUB_DELAY_MS:-800} python3 -m uvicorn app.llm_stub:app --port 9000 --log-level warning > /tmp/stub.log 2>&1 &
SP=$!
LLM_PROVIDER=openai_compat LLM_BASE_URL=http://localhost:9000/v1 LLM_MODEL=stub CACHE_RUNS=$C RATE_LIMIT_PER_MIN=1000000 LOCAL_WORKERS=$W \
  python3 -m uvicorn app.main:app --port 8000 --log-level warning > /tmp/api.log 2>&1 &
AP=$!
sleep 4
( sleep 28; ps -o %cpu= -p $AP > /tmp/cpu.txt ) &
locust -f loadtest/locustfile.py --headless -u "$U" -r "$U" -t "$D" --host http://localhost:8000 --only-summary \
  --csv "loadtest/results/w${W}_u${U}_c${C}" > /tmp/locust_out.txt 2>&1
kill $AP $SP 2>/dev/null; wait 2>/dev/null
python3 - "$W" "$U" "$C" <<'PY'
import csv, sys
w, u, c = sys.argv[1:4]
for r in csv.DictReader(open(f"loadtest/results/w{w}_u{u}_c{c}_stats.csv")):
    if r["Name"].startswith("run_total"):
        print(f"workers={w:>2} offered={u:>2}/s cache={c} | runs={r['Request Count']:>4} fail={r['Failure Count']:>3} | RPS={float(r['Requests/s']):5.2f} | p50={float(r['50%'])/1000:6.2f}s p95={float(r['95%'])/1000:6.2f}s max={float(r['Max Response Time'])/1000:6.2f}s")
PY
echo "   api process cpu sample: $(cat /tmp/cpu.txt 2>/dev/null)% (100 = one core)"
