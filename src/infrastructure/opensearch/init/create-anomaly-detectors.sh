#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-900}"
MIN_DOCUMENTS="${DETECTOR_MIN_DOCUMENTS:-60}"
FORCE_RECREATE="${DETECTOR_FORCE_RECREATE:-false}"
HOSTS="${DETECTOR_HOSTS:-traffic-generator api-gateway processing-service data-service worker-service}"
LEGACY_HOSTS="${LEGACY_DETECTOR_HOSTS:-machine-01 machine-02 machine-03 machine-04 machine-05}"

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
  index_pattern="metrics-${host}-*"

  request -X POST "${OPENSEARCH_URL}/${index_pattern}/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":[{\"term\":{\"measurement_name\":\"${measurement}\"}},{\"exists\":{\"field\":\"${field}\"}}]}}}" \
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
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_search?pretty" \
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
    echo "Removed detector ${name} (${detector_id})."
  done
}

remove_legacy_detectors() {
  for legacy_host in $LEGACY_HOSTS; do
    remove_detectors_by_name "CPU-${legacy_host}"
    remove_detectors_by_name "RAM-${legacy_host}"
  done

  remove_detectors_by_name "CPU_ANOMALY"
  remove_detectors_by_name "RAM_ANOMALY"
  remove_detectors_by_name "infrastructure-cpu-usage"
  remove_detectors_by_name "infrastructure-memory-usage"
  remove_detectors_by_name "thesis-cpu-anomaly-detector"
  remove_detectors_by_name "thesis-memory-anomaly-detector"
}

remove_current_detectors() {
  for host in $HOSTS; do
    remove_detectors_by_name "CPU-${host}"
    remove_detectors_by_name "RAM-${host}"
  done
}

create_detector() {
  name="$1"
  description="$2"
  host="$3"
  measurement="$4"
  field="$5"
  feature_name="$6"
  aggregation_name="$7"
  index_pattern="metrics-${host}-*"

  cat >/tmp/detector.json <<JSON
{
  "name": "${name}",
  "description": "${description}",
  "time_field": "@timestamp",
  "indices": ["${index_pattern}"],
  "filter_query": {
    "bool": {
      "filter": [
        {"term": {"measurement_name": "${measurement}"}},
        {"exists": {"field": "${field}"}}
      ]
    }
  },
  "detection_interval": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "window_delay": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "shingle_size": 8,
  "schema_version": 0,
  "feature_attributes": [
    {
      "feature_name": "${feature_name}",
      "feature_enabled": true,
      "aggregation_query": {
        "${aggregation_name}": {
          "avg": {"field": "${field}"}
        }
      }
    }
  ]
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
    echo "Unable to read detector ID for ${name}." >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi

  start_detector "$detector_id"
  echo "Created SINGLE_ENTITY detector ${name} (${detector_id}) on ${index_pattern}."
}

ensure_detector() {
  name="$1"
  description="$2"
  host="$3"
  measurement="$4"
  field="$5"
  feature_name="$6"
  aggregation_name="$7"

  ids="$(find_detector_ids "$name" 2>/dev/null || true)"

  if [ -z "$ids" ]; then
    create_detector "$name" "$description" "$host" "$measurement" "$field" "$feature_name" "$aggregation_name"
    return 0
  fi

  set -- $ids
  detector_id="$1"
  shift

  for duplicate_id in "$@"; do
    delete_detector_id "$duplicate_id"
    echo "Removed duplicate detector ${name} (${duplicate_id})."
  done

  start_detector "$detector_id"
  echo "Reusing existing ${name} detector (${detector_id})."
}

wait_for_plugin
remove_legacy_detectors

if [ "$FORCE_RECREATE" = "true" ]; then
  echo "Force recreation enabled: removing current SINGLE_ENTITY detectors."
  remove_current_detectors
fi

wait_for_metric_on_all_hosts CPU cpu cpu.usage_active
wait_for_metric_on_all_hosts RAM mem mem.used_percent

for host in $HOSTS; do
  ensure_detector \
    "CPU-${host}" \
    "Active CPU usage anomaly detector for ${host}" \
    "$host" \
    cpu \
    cpu.usage_active \
    CPU_ANOMALY \
    cpu_anomaly

  ensure_detector \
    "RAM-${host}" \
    "Memory usage anomaly detector for ${host}" \
    "$host" \
    mem \
    mem.used_percent \
    RAM_ANOMALY \
    ram_anomaly
done

echo "Single-entity CPU and RAM anomaly detectors are ready for all monitored services."
