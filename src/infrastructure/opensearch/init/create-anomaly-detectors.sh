#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-300}"
MIN_DOCUMENTS="${DETECTOR_MIN_DOCUMENTS:-20}"
HOSTS="${DETECTOR_HOSTS:-machine-01 machine-02 machine-03 machine-04 machine-05}"
INDEX_PATTERN="metrics-machine-*"
CATEGORY_FIELD="tag.host_id"

CPU_NAME="CPU_ANOMALY"
MEMORY_NAME="RAM_ANOMALY"

# Names used by the first infrastructure-clean-baseline revision. They are
# removed once so that Dashboards exposes only the canonical detector names.
LEGACY_CPU_NAME="infrastructure-cpu-usage"
LEGACY_MEMORY_NAME="infrastructure-memory-usage"

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
    echo "Waiting for the OpenSearch Anomaly Detection plugin..."
    sleep 3
    elapsed=$((elapsed + 3))
  done
  echo "OpenSearch Anomaly Detection plugin is available."
}

metric_count() {
  host="$1"
  measurement="$2"
  field="$3"

  request -X POST "${OPENSEARCH_URL}/${INDEX_PATTERN}/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":[{\"term\":{\"measurement_name\":\"${measurement}\"}},{\"term\":{\"${CATEGORY_FIELD}\":\"${host}\"}},{\"exists\":{\"field\":\"${field}\"}}]}}}" \
    | sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    | head -n 1
}

wait_for_metric_on_all_hosts() {
  label="$1"
  measurement="$2"
  field="$3"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    all_ready=1

    for host in $HOSTS; do
      count="$(metric_count "$host" "$measurement" "$field" 2>/dev/null || echo 0)"
      count="${count:-0}"

      if [ "$count" -lt "$MIN_DOCUMENTS" ]; then
        all_ready=0
        echo "Waiting for ${label} documents on ${host} (${count}/${MIN_DOCUMENTS})..."
      fi
    done

    if [ "$all_ready" -eq 1 ]; then
      echo "${label}: every monitored host has at least ${MIN_DOCUMENTS} valid documents."
      return 0
    fi

    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "Not enough ${label} documents arrived on every monitored host within ${WAIT_SECONDS}s." >&2
  exit 1
}

find_detector_ids() {
  name="$1"
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_search" \
    -H "Content-Type: application/json" \
    -d "{\"size\":100,\"_source\":false,\"query\":{\"match_phrase\":{\"name\":\"${name}\"}}}" \
    | sed -n 's/^[[:space:]]*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

stop_detector() {
  detector_id="$1"
  curl -sS -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop" \
    >/dev/null 2>&1 || true
}

start_detector() {
  detector_id="$1"
  # Starting an already running detector can return a non-2xx response on some
  # OpenSearch versions. The detector already being active is an acceptable
  # idempotent outcome, so the request is intentionally tolerant here.
  curl -sS -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" \
    >/dev/null 2>&1 || true
}

delete_detector_id() {
  detector_id="$1"
  stop_detector "$detector_id"
  curl -sS -X DELETE "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" \
    >/dev/null 2>&1 || true
}

remove_detectors_by_name() {
  name="$1"
  for detector_id in $(find_detector_ids "$name" 2>/dev/null || true); do
    delete_detector_id "$detector_id"
    echo "Removed legacy or duplicate detector ${name} (${detector_id})."
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

  start_detector "$detector_id"
  echo "Created and started ${name} (${detector_id})."
}

ensure_detector() {
  name="$1"
  description="$2"
  measurement="$3"
  field="$4"
  feature="$5"

  ids="$(find_detector_ids "$name" 2>/dev/null || true)"

  if [ -z "$ids" ]; then
    create_detector "$name" "$description" "$measurement" "$field" "$feature"
    return 0
  fi

  # Preserve the first detector and its learned model across Docker restarts.
  # Any accidental duplicates are removed.
  set -- $ids
  detector_id="$1"
  shift

  for duplicate_id in "$@"; do
    delete_detector_id "$duplicate_id"
    echo "Removed duplicate ${name} detector (${duplicate_id})."
  done

  start_detector "$detector_id"
  echo "Reusing existing ${name} detector (${detector_id}); model state preserved."
}

wait_for_plugin

# High-cardinality detectors use tag.host_id as the entity dimension. Enabling
# cold-start interpolation shortens initialization in the local thesis lab.
curl -sS -X PUT "${OPENSEARCH_URL}/_cluster/settings" \
  -H "Content-Type: application/json" \
  -d '{"persistent":{"plugins.anomaly_detection.hcad_cold_start_interpolation.enabled":true}}' \
  >/dev/null || true

wait_for_metric_on_all_hosts CPU cpu cpu.usage_active
wait_for_metric_on_all_hosts RAM mem mem.used_percent

# One-time cleanup of names used before the observability baseline was frozen.
remove_detectors_by_name "$LEGACY_CPU_NAME"
remove_detectors_by_name "$LEGACY_MEMORY_NAME"

ensure_detector \
  "$CPU_NAME" \
  "Average active CPU usage grouped by monitored machine" \
  cpu \
  cpu.usage_active \
  average_cpu_usage_active

ensure_detector \
  "$MEMORY_NAME" \
  "Average memory usage grouped by monitored machine" \
  mem \
  mem.used_percent \
  average_memory_used_percent

echo "CPU_ANOMALY and RAM_ANOMALY are ready."
