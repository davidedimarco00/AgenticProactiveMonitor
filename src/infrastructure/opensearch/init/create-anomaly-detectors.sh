#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-300}"
MIN_DOCUMENTS="${DETECTOR_MIN_DOCUMENTS:-20}"
INDEX_PATTERN="metrics-machine-*"
CATEGORY_FIELD="tag.host_id"

CPU_NAME="infrastructure-cpu-usage"
MEMORY_NAME="infrastructure-memory-usage"

request() {
  curl -fsS "$@"
}

wait_for_plugin() {
  elapsed=0
  until request "${OPENSEARCH_URL}/_plugins/_anomaly_detection/stats" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$WAIT_SECONDS" ]; then
      echo "Anomaly Detection plugin was not ready after ${WAIT_SECONDS}s." >&2
      exit 1
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
}

metric_count() {
  measurement="$1"
  field="$2"
  request -X POST "${OPENSEARCH_URL}/${INDEX_PATTERN}/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":[{\"term\":{\"measurement_name\":\"${measurement}\"}},{\"exists\":{\"field\":\"${field}\"}},{\"exists\":{\"field\":\"${CATEGORY_FIELD}\"}}]}}}" \
    | sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    | head -n 1
}

wait_for_metric() {
  label="$1"
  measurement="$2"
  field="$3"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    count="$(metric_count "$measurement" "$field" 2>/dev/null || echo 0)"
    count="${count:-0}"
    if [ "$count" -ge "$MIN_DOCUMENTS" ]; then
      echo "${label}: ${count} valid documents available."
      return 0
    fi
    echo "Waiting for ${label} documents (${count}/${MIN_DOCUMENTS})..."
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "Not enough ${label} documents arrived." >&2
  exit 1
}

find_detector_ids() {
  name="$1"
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_search?pretty" \
    -H "Content-Type: application/json" \
    -d "{\"size\":100,\"_source\":false,\"query\":{\"match_phrase\":{\"name\":\"${name}\"}}}" \
    | sed -n 's/^[[:space:]]*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

delete_existing_detector() {
  name="$1"
  for detector_id in $(find_detector_ids "$name" 2>/dev/null || true); do
    curl -sS -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop" >/dev/null 2>&1 || true
    curl -sS -X DELETE "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" >/dev/null 2>&1 || true
    echo "Removed previous detector ${name} (${detector_id})."
  done
}

create_detector() {
  name="$1"
  description="$2"
  measurement="$3"
  field="$4"
  feature="$5"

  cat >/tmp/detector.json <<JSON
{
  "name": "${name}",
  "description": "${description}",
  "time_field": "@timestamp",
  "indices": ["${INDEX_PATTERN}"],
  "shingle_size": 4,
  "schema_version": 0,
  "feature_attributes": [
    {
      "feature_name": "${feature}",
      "feature_enabled": true,
      "aggregation_query": {
        "${feature}": {"avg": {"field": "${field}"}}
      }
    }
  ],
  "filter_query": {
    "bool": {
      "filter": [
        {"term": {"measurement_name": "${measurement}"}},
        {"exists": {"field": "${field}"}},
        {"exists": {"field": "${CATEGORY_FIELD}"}}
      ]
    }
  },
  "detection_interval": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "window_delay": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "category_field": ["${CATEGORY_FIELD}"]
}
JSON

  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_validate/detector" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json >/tmp/detector-validation.json

  response="$(request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json)"

  detector_id="$(printf '%s' "$response" | sed -n 's/.*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  if [ -z "$detector_id" ]; then
    echo "Unable to read the detector ID for ${name}." >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi

  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" >/dev/null
  echo "Created and started ${name} (${detector_id})."
}

wait_for_plugin

# This setting shortens the HCAD cold-start phase in the local thesis lab.
curl -sS -X PUT "${OPENSEARCH_URL}/_cluster/settings" \
  -H "Content-Type: application/json" \
  -d '{"persistent":{"plugins.anomaly_detection.hcad_cold_start_interpolation.enabled":true}}' \
  >/dev/null || true

wait_for_metric CPU cpu cpu.usage_active
wait_for_metric memory mem mem.used_percent

delete_existing_detector "$CPU_NAME"
delete_existing_detector "$MEMORY_NAME"

create_detector \
  "$CPU_NAME" \
  "Average active CPU usage grouped by monitored machine" \
  cpu \
  cpu.usage_active \
  average_cpu_usage_active

create_detector \
  "$MEMORY_NAME" \
  "Average memory usage grouped by monitored machine" \
  mem \
  mem.used_percent \
  average_memory_used_percent

echo "CPU and memory anomaly detectors are ready."
