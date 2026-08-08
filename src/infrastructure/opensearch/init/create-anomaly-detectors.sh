#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-3600}"
REQUIRED_INTERVALS="${DETECTOR_REQUIRED_INTERVALS:-40}"
HOSTS="${DETECTOR_HOSTS:-traffic-generator api-gateway processing-service data-service worker-service}"

DETECTION_INTERVAL_MINUTES=1
WINDOW_DELAY_MINUTES=1
SHINGLE_SIZE=4
LOOKBACK_MINUTES=$((REQUIRED_INTERVALS + WINDOW_DELAY_MINUTES + 10))

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

complete_interval_count() {
  host="$1"
  measurement="$2"
  field="$3"
  index_pattern="metrics-${host}-*"

  response="$(request -X POST "${OPENSEARCH_URL}/${index_pattern}/_search?ignore_unavailable=true" \
    -H "Content-Type: application/json" \
    -d "{\"size\":0,\"query\":{\"bool\":{\"filter\":[{\"range\":{\"@timestamp\":{\"gte\":\"now-${LOOKBACK_MINUTES}m/m\",\"lt\":\"now-${WINDOW_DELAY_MINUTES}m/m\"}}},{\"term\":{\"measurement_name\":\"${measurement}\"}},{\"exists\":{\"field\":\"${field}\"}}]}},\"aggs\":{\"per_interval\":{\"date_histogram\":{\"field\":\"@timestamp\",\"fixed_interval\":\"1m\",\"min_doc_count\":1}}}}" 2>/dev/null || true)"

  if [ -z "$response" ]; then
    echo 0
    return
  fi

  printf '%s' "$response" | grep -o '"key_as_string"' | wc -l | tr -d ' '
}

wait_for_history_on_all_hosts() {
  label="$1"
  measurement="$2"
  field="$3"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    all_ready=1

    for host in $HOSTS; do
      intervals="$(complete_interval_count "$host" "$measurement" "$field" 2>/dev/null || echo 0)"
      intervals="${intervals:-0}"

      if [ "$intervals" -lt "$REQUIRED_INTERVALS" ]; then
        all_ready=0
        echo "Waiting for ${label} baseline on ${host} (${intervals}/${REQUIRED_INTERVALS} complete 1-minute intervals)..."
      fi
    done

    if [ "$all_ready" -eq 1 ]; then
      echo "${label}: every monitored service has ${REQUIRED_INTERVALS} complete 1-minute intervals."
      return 0
    fi

    sleep 10
    elapsed=$((elapsed + 10))
  done

  echo "Not enough ${label} baseline history arrived within ${WAIT_SECONDS}s." >&2
  exit 1
}

find_detector_ids() {
  name="$1"
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_search" \
    -H "Content-Type: application/json" \
    -d "{\"size\":100,\"_source\":false,\"query\":{\"match_phrase\":{\"name\":\"${name}\"}}}" \
    | sed -n 's/^[[:space:]]*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

start_detector() {
  detector_id="$1"
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" >/dev/null
}

create_detector() {
  name="$1"
  description="$2"
  host="$3"
  field="$4"
  feature_name="$5"
  aggregation_name="$6"
  index_pattern="metrics-${host}-*"

  cat >/tmp/detector.json <<JSON
{
  "name": "${name}",
  "description": "${description}",
  "time_field": "@timestamp",
  "indices": ["${index_pattern}"],
  "filter_query": {
    "match_all": {}
  },
  "feature_attributes": [
    {
      "feature_name": "${feature_name}",
      "feature_enabled": true,
      "aggregation_query": {
        "${aggregation_name}": {
          "avg": {
            "field": "${field}"
          }
        }
      }
    }
  ],
  "detection_interval": {
    "period": {
      "interval": ${DETECTION_INTERVAL_MINUTES},
      "unit": "Minutes"
    }
  },
  "window_delay": {
    "period": {
      "interval": ${WINDOW_DELAY_MINUTES},
      "unit": "Minutes"
    }
  },
  "shingle_size": ${SHINGLE_SIZE}
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
  field="$4"
  feature_name="$5"
  aggregation_name="$6"

  ids="$(find_detector_ids "$name" 2>/dev/null || true)"
  if [ -n "$ids" ]; then
    detector_id="$(printf '%s\n' "$ids" | head -n 1)"
    start_detector "$detector_id" || true
    echo "Reusing existing SINGLE_ENTITY detector ${name} (${detector_id})."
    return 0
  fi

  create_detector "$name" "$description" "$host" "$field" "$feature_name" "$aggregation_name"
}

wait_for_plugin
wait_for_history_on_all_hosts CPU cpu cpu.usage_active
wait_for_history_on_all_hosts RAM mem mem.used_percent

for host in $HOSTS; do
  ensure_detector \
    "CPU-${host}" \
    "Active CPU usage anomaly detector for ${host}" \
    "$host" \
    cpu.usage_active \
    CPU_ANOMALY \
    cpu_anomaly

  ensure_detector \
    "RAM-${host}" \
    "Memory usage anomaly detector for ${host}" \
    "$host" \
    mem.used_percent \
    RAM_ANOMALY \
    ram_anomaly
done

echo "All SINGLE_ENTITY CPU and RAM detectors are created and started."
