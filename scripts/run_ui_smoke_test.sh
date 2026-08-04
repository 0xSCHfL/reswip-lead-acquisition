#!/usr/bin/env bash
set -euo pipefail

api_url="${RESWIP_UI_API_URL:-http://localhost:8000}"
input_path="${RESWIP_UI_SMOKE_INPUT:-}"

curl --fail --silent "${api_url}/health" >/dev/null

if [[ -z "${input_path}" ]]; then
  echo "RESWIP_UI_SMOKE_INPUT must point to a small CSV/XLSX fixture under the configured input directory" >&2
  exit 2
fi

payload=$(python - "$input_path" <<'PY'
import json
import sys

print(json.dumps({
    "workflow": "enrich_existing",
    "input_path": sys.argv[1],
    "profile_path": "profiles/energy.yaml",
    "enricher": "both",
    "use_kbo": False,
    "use_pappers_fallback": False,
    "deduplicate": True,
    "output_format": "csv",
}))
PY
)
job_id=$(curl --fail --silent -H 'Content-Type: application/json' -d "$payload" "${api_url}/api/jobs" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

for _ in $(seq 1 120); do
  response=$(curl --fail --silent "${api_url}/api/jobs/${job_id}")
  status=$(python -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$response")
  case "$status" in
    completed|completed_with_warnings)
      python -c 'import json,sys; data=json.load(sys.stdin); assert data["artifacts"], "no output artifacts"' <<<"$response"
      echo "UI smoke test passed: ${job_id}"
      exit 0
      ;;
    failed|cancelled)
      echo "$response" >&2
      exit 1
      ;;
  esac
  sleep 1
done

echo "Timed out waiting for job ${job_id}" >&2
exit 1
