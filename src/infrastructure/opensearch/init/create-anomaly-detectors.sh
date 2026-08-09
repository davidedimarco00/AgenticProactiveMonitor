#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-3600}"
REQUIRED_INTERVALS="${DETECTOR_REQUIRED_INTERVALS:-40}"
HOSTS="${DETECTOR_HOSTS:-traffic-generator api-gateway processing-service data-service worker-service}"
NETWORK_LATENCY_HOSTS="traffic-generator api-gateway processing-service"

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

wait_for_history_on_hosts() {
  label="$1"
  measurement="$2"
  field="$3"
  host_list="$4"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    all_ready=1

    for host in $host_list; do
      intervals="$(complete_interval_count "$host" "$measurement" "$field" 2>/dev/null || echo 0)"
      intervals="${intervals:-0}"

      if [ "$intervals" -lt "$REQUIRED_INTERVALS" ]; then
        all_ready=0
        echo "Waiting for ${label} baseline on ${host} (${intervals}/${REQUIRED_INTERVALS} complete 1-minute intervals)..."
      fi
    done

    if [ "$all_ready" -eq 1 ]; then
      echo "${label}: every required source has ${REQUIRED_INTERVALS} complete 1-minute intervals."
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
    -d "{\"size\":100,\"_source\":[\"name\"],\"query\":{\"match_phrase\":{\"name\":\"${name}\"}}}" \
    | grep -o '"_id"[[:space:]]*:[[:space:]]*"[^"]*"' \
    | sed 's/.*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/'
}

start_detector() {
  detector_id="$1"
  response_file="/tmp/start-detector-${detector_id}.json"

  http_code="$(curl -sS -o "$response_file" -w '%{http_code}' \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start")"

  case "$http_code" in
    200|201)
      return 0
      ;;
    409)
      echo "Detector ${detector_id} is already running."
      return 0
      ;;
    *)
      echo "Unable to start detector ${detector_id} (HTTP ${http_code})." >&2
      cat "$response_file" >&2 || true
      return 1
      ;;
  esac
}

stop_detector() {
  detector_id="$1"
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop" >/dev/null 2>&1 || true
  request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop?historical=true" >/dev/null 2>&1 || true
}

detector_uses_field() {
  detector_id="$1"
  field="$2"

  body="$(request "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" 2>/dev/null || true)"
  printf '%s' "$body" | tr -d '[:space:]' | grep -Fq "\"field\":\"${field}\""
}

write_detector_json() {
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
    "term": {
      "measurement_name": "${measurement}"
    }
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

  write_detector_json "$name" "$description" "$host" "$measurement" "$field" "$feature_name" "$aggregation_name"

  response="$(request -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json)"

  detector_id="$(printf '%s' "$response" | grep -o '"_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 | sed 's/.*"_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')"

  if [ -z "$detector_id" ]; then
    echo "Unable to read detector ID for ${name}." >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi

  start_detector "$detector_id"
  echo "Created SINGLE_ENTITY detector ${name} (${detector_id}) on ${index_pattern}."
}

update_detector() {
  detector_id="$1"
  name="$2"
  description="$3"
  host="$4"
  measurement="$5"
  field="$6"
  feature_name="$7"
  aggregation_name="$8"

  echo "Migrating ${name} (${detector_id}) to metric ${field}."
  stop_detector "$detector_id"
  write_detector_json "$name" "$description" "$host" "$measurement" "$field" "$feature_name" "$aggregation_name"

  request -X PUT "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json >/dev/null

  start_detector "$detector_id"
  echo "Updated and restarted SINGLE_ENTITY detector ${name} (${detector_id})."
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
  if [ -n "$ids" ]; then
    detector_id="$(printf '%s\n' "$ids" | head -n 1)"

    if detector_uses_field "$detector_id" "$field"; then
      start_detector "$detector_id"
      echo "Reusing existing SINGLE_ENTITY detector ${name} (${detector_id}) with ${field}."
    else
      update_detector "$detector_id" "$name" "$description" "$host" "$measurement" "$field" "$feature_name" "$aggregation_name"
    fi
    return 0
  fi

  create_detector "$name" "$description" "$host" "$measurement" "$field" "$feature_name" "$aggregation_name"
}

wait_for_plugin
wait_for_history_on_hosts CPU docker_container_cpu docker_container_cpu.usage_percent "$HOSTS"
wait_for_history_on_hosts RAM docker_container_mem docker_container_mem.usage_percent "$HOSTS"
wait_for_history_on_hosts NETLAT network_service_latency network_service_latency.response_time "$NETWORK_LATENCY_HOSTS"

for host in $HOSTS; do
  ensure_detector \
    "CPU-${host}" \
    "Container CPU usage anomaly detector for ${host}" \
    "$host" \
    docker_container_cpu \
    docker_container_cpu.usage_percent \
    CPU_ANOMALY \
    cpu_anomaly

  ensure_detector \
    "RAM-${host}" \
    "Container memory usage anomaly detector for ${host}" \
    "$host" \
    docker_container_mem \
    docker_container_mem.usage_percent \
    RAM_ANOMALY \
    ram_anomaly
done

ensure_detector \
  "NETLAT-traffic-generator-api-gateway" \
  "End-to-end network service latency detector from traffic-generator to api-gateway" \
  traffic-generator \
  network_service_latency \
  network_service_latency.response_time \
  NETWORK_LATENCY_ANOMALY \
  network_latency_anomaly

ensure_detector \
  "NETLAT-api-gateway-processing-service" \
  "End-to-end network service latency detector from api-gateway to processing-service" \
  api-gateway \
  network_service_latency \
  network_service_latency.response_time \
  NETWORK_LATENCY_ANOMALY \
  network_latency_anomaly

ensure_detector \
  "NETLAT-processing-service-data-service" \
  "End-to-end network service latency detector from processing-service to data-service" \
  processing-service \
  network_service_latency \
  network_service_latency.response_time \
  NETWORK_LATENCY_ANOMALY \
  network_latency_anomaly

echo "All detectors are SINGLE_ENTITY: 10 container CPU/RAM detectors and 3 network-latency detectors."
