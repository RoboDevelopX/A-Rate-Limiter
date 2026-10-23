#!/usr/bin/env bash
# End-to-end check against a running demo (make up): five allowed calls, then a 429.
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:8000}"

predict() {
  curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/predict" \
    -H 'X-API-Key: demo-free-key' -H 'Content-Type: application/json' \
    -d '{"model": "small-model", "prompt": "Hello!", "max_tokens": 32}'
}

curl -sf "$BASE_URL/healthz" | grep -q '"redis":"up"' || { echo "healthz: Redis not up"; exit 1; }
for i in 1 2 3 4 5; do
  code=$(predict)
  [ "$code" = 200 ] || { echo "request $i: expected 200, got $code"; exit 1; }
done
code=$(predict)
[ "$code" = 429 ] || { echo "request 6: expected 429, got $code"; exit 1; }
curl -sf "$BASE_URL/metrics" | grep -q 'ratelimit_decisions_total{decision="blocked",limiter="free:default"}' \
  || { echo "metrics: no blocked decision recorded"; exit 1; }
echo "smoke test passed: 5 x 200, then 429, metrics recorded"
