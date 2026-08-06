#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WARMUP_ENABLED="${AD_WARMUP_ENABLED:-true}"
WARMUP_MINUTES="${AD_WARMUP_MINUTES:-60}"
END_OFFSET_MINUTES="${AD_WARMUP_END_OFFSET_MINUTES:-3}"
WARMUP_INDEX="metrics-ad-warmup"

if [ "${WARMUP_ENABLED}" != "true" ]; then
  echo "Anomaly detector warm-up is disabled."
  exit 0
fi

case "${WARMUP_MINUTES}" in
  ''|*[!0-9]*) echo "AD_WARMUP_MINUTES must be a positive integer." >&2; exit 1 ;;
esac

if [ "${WARMUP_MINUTES}" -lt 40 ]; then
  echo "AD_WARMUP_MINUTES must be at least 40 for the OpenSearch cold start." >&2
  exit 1
fi

case "${END_OFFSET_MINUTES}" in
  ''|*[!0-9]*) echo "AD_WARMUP_END_OFFSET_MINUTES must be an integer." >&2; exit 1 ;;
esac

echo "Waiting for OpenSearch before generating detector warm-up data..."
until curl -fsS "${OPENSEARCH_URL}/_cluster/health" >/dev/null; do
  sleep 3
done

# The index is recreated on every laboratory bootstrap so the warm-up dataset
# remains bounded and its timestamps are always close to the current time.
curl -sS -X DELETE "${OPENSEARCH_URL}/${WARMUP_INDEX}" >/dev/null 2>&1 || true

NOW_SECONDS="$(date +%s)"
FIRST_SAMPLE_SECONDS=$((NOW_SECONDS - (END_OFFSET_MINUTES * 60) - ((WARMUP_MINUTES - 1) * 60)))
BULK_FILE="/tmp/ad-warmup.ndjson"
: >"${BULK_FILE}"

machine_role() {
  case "$1" in
    1) echo "application-server" ;;
    2) echo "database-server" ;;
    3) echo "api-gateway" ;;
    4) echo "worker-node" ;;
    5) echo "edge-node" ;;
  esac
}

HOST_NUMBER=1
while [ "${HOST_NUMBER}" -le 5 ]; do
  HOST_ID="$(printf 'machine-%02d' "${HOST_NUMBER}")"
  ROLE="$(machine_role "${HOST_NUMBER}")"
  SAMPLE_NUMBER=0

  while [ "${SAMPLE_NUMBER}" -lt "${WARMUP_MINUTES}" ]; do
    TIMESTAMP_MS=$(((FIRST_SAMPLE_SECONDS + (SAMPLE_NUMBER * 60)) * 1000))

    # Deterministic, non-constant baselines provide enough variation for RCF
    # without introducing an anomaly during model initialization.
    CPU_ACTIVE=$((14 + ((SAMPLE_NUMBER * 7 + HOST_NUMBER * 3) % 13)))
    CPU_USER=$((CPU_ACTIVE * 65 / 100))
    CPU_SYSTEM=$((CPU_ACTIVE - CPU_USER))
    CPU_IDLE=$((100 - CPU_ACTIVE))

    MEMORY_PERCENT=$((43 + ((SAMPLE_NUMBER * 5 + HOST_NUMBER * 2) % 10)))
    MEMORY_TOTAL=8589934592
    MEMORY_USED=$((MEMORY_TOTAL * MEMORY_PERCENT / 100))
    MEMORY_AVAILABLE=$((MEMORY_TOTAL - MEMORY_USED))
    MEMORY_AVAILABLE_PERCENT=$((100 - MEMORY_PERCENT))

    printf '%s\n' "{\"index\":{\"_index\":\"${WARMUP_INDEX}\",\"_id\":\"cpu-${HOST_ID}-${TIMESTAMP_MS}\"}}" >>"${BULK_FILE}"
    printf '%s\n' "{\"@timestamp\":${TIMESTAMP_MS},\"measurement_name\":\"cpu\",\"tag\":{\"host\":\"${HOST_ID}\",\"host_id\":\"${HOST_ID}\",\"machine_role\":\"${ROLE}\",\"metric_type\":\"cpu\",\"environment\":\"thesis-lab\",\"project\":\"AgenticProactiveMonitor\",\"monitored_by\":\"ad-warmup\",\"synthetic_warmup\":\"true\"},\"cpu\":{\"usage_active\":${CPU_ACTIVE},\"usage_idle\":${CPU_IDLE},\"usage_user\":${CPU_USER},\"usage_system\":${CPU_SYSTEM}}}" >>"${BULK_FILE}"

    printf '%s\n' "{\"index\":{\"_index\":\"${WARMUP_INDEX}\",\"_id\":\"mem-${HOST_ID}-${TIMESTAMP_MS}\"}}" >>"${BULK_FILE}"
    printf '%s\n' "{\"@timestamp\":${TIMESTAMP_MS},\"measurement_name\":\"mem\",\"tag\":{\"host\":\"${HOST_ID}\",\"host_id\":\"${HOST_ID}\",\"machine_role\":\"${ROLE}\",\"metric_type\":\"memory\",\"environment\":\"thesis-lab\",\"project\":\"AgenticProactiveMonitor\",\"monitored_by\":\"ad-warmup\",\"synthetic_warmup\":\"true\"},\"mem\":{\"total\":${MEMORY_TOTAL},\"available\":${MEMORY_AVAILABLE},\"used\":${MEMORY_USED},\"used_percent\":${MEMORY_PERCENT},\"available_percent\":${MEMORY_AVAILABLE_PERCENT}}}" >>"${BULK_FILE}"

    SAMPLE_NUMBER=$((SAMPLE_NUMBER + 1))
  done

  HOST_NUMBER=$((HOST_NUMBER + 1))
done

STATUS="$(curl -sS -o /tmp/ad-warmup-response.json -w '%{http_code}' \
  -X POST "${OPENSEARCH_URL}/_bulk?refresh=true" \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @"${BULK_FILE}" || true)"

if [ "${STATUS}" != "200" ]; then
  echo "Unable to index anomaly detector warm-up data (HTTP ${STATUS})." >&2
  cat /tmp/ad-warmup-response.json >&2 || true
  exit 1
fi

if grep -q '"errors":true' /tmp/ad-warmup-response.json; then
  echo "OpenSearch rejected one or more warm-up documents." >&2
  cat /tmp/ad-warmup-response.json >&2
  exit 1
fi

EXPECTED_DOCUMENTS=$((WARMUP_MINUTES * 5 * 2))
COUNT="$(curl -fsS "${OPENSEARCH_URL}/${WARMUP_INDEX}/_count" | sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)"
COUNT="${COUNT:-0}"

if [ "${COUNT}" -ne "${EXPECTED_DOCUMENTS}" ]; then
  echo "Warm-up document count mismatch: expected ${EXPECTED_DOCUMENTS}, found ${COUNT}." >&2
  exit 1
fi

echo "Created ${COUNT} warm-up metric documents (${WARMUP_MINUTES} minutes for CPU and RAM across five machines)."
echo "OpenSearch detectors can now complete their cold start without waiting approximately 32 real-time minutes."
