#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://localhost:8000}"

echo "GET ${URL}/health"
curl -sf "${URL}/health" | tee /dev/stderr | grep -q '"status":"ok"'

echo
echo "POST ${URL}/optimize-energy (sample request)"
curl -sf -X POST "${URL}/optimize-energy" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_request.json | python -m json.tool

echo
echo "Smoke test passed."
