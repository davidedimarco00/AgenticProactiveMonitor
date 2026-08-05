#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
CONFIG_INDEX="agentic-detector-config"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-180}"

json_string_value() {
  key="$1"
  sed -n "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

wait_for_plugin() {
  elapsed=0
  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    status="$(curl -sS -o /tmp/ad-stats.json -w "%{http_code}" \
      "${OPENSEARCH_URL}/_plugins/_anomaly_detection/stats")"
    if [ "${status}" = "200" ]; then
      echo "OpenSearch Anomaly Detection plugin is available."
      return 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  echo "Anomaly Detection plugin did not become available." >&2
  cat /tmp/ad-stats.json >&2 || true
  return 1
}

metric_count() {
  metric_type="$1"
  field="$2"
  status="$(curl -sS -o /tmp/metric-count.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/metrics-*/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":[{\"term\":{\"metric_type\":\"${metric_type}\"}},{\"exists\":{\"field\":\"${field}\"}}]}}}")"
  if [ "${status}" != "200" ]; then
    echo 0
    return 0
  fi
  sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' /tmp/metric-count.json | head -n 1
}

wait_for_metric() {
  metric_type="$1"
  field="$2"
  elapsed=0
  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    count="$(metric_count "${metric_type}" "${field}")"
    count="${count:-0}"
    if [ "${count}" -gt 0 ]; then
      echo "Found ${count} ${metric_type} metric documents using field ${field}."
      return 0
    fi
    echo "Waiting for ${metric_type}.${field} telemetry..."
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo "No ${metric_type}.${field} telemetry arrived within ${WAIT_SECONDS}s." >&2
  echo "Check the monitored-machine Telegraf logs and the metrics-* mappings." >&2
  return 1
}

create_config_index() {
  status="$(curl -sS -o /tmp/config-index.json -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${CONFIG_INDEX}" \
    -H "Content-Type: application/json" \
    -d '{"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"properties":{"detector_id":{"type":"keyword"},"name":{"type":"keyword"},"metric_field":{"type":"keyword"},"metric_type":{"type":"keyword"}}}}')"
  case "${status}" in
    200|201|400) return 0 ;;
    *)
      echo "Unable to create ${CONFIG_INDEX} (HTTP ${status})." >&2
      cat /tmp/config-index.json >&2
      return 1
      ;;
  esac
}

stored_detector_id() {
  key="$1"
  status="$(curl -sS -o /tmp/stored-detector.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}")"
  if [ "${status}" = "200" ]; then
    json_string_value detector_id </tmp/stored-detector.json
  fi
}

detector_exists() {
  detector_id="$1"
  status="$(curl -sS -o /tmp/detector-get.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}")"
  [ "${status}" = "200" ]
}

start_detector() {
  detector_id="$1"
  name="$2"
  status="$(curl -sS -o /tmp/detector-start.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start")"
  case "${status}" in
    200|201)
      echo "Started detector ${name} (${detector_id})."
      ;;
    400)
      # OpenSearch returns 400 when a real-time detector is already running.
      echo "Detector ${name} is already running or already scheduled."
      ;;
    *)
      echo "Unable to start detector ${name} (HTTP ${status})." >&2
      cat /tmp/detector-start.json >&2
      return 1
      ;;
  esac
}

ensure_detector() {
  key="$1"
  name="$2"
  metric_type="$3"
  metric_field="$4"
  feature_name="$5"

  detector_id="$(stored_detector_id "${key}" || true)"
  if [ -n "${detector_id}" ] && ! detector_exists "${detector_id}"; then
    echo "Stored detector ${detector_id} no longer exists; recreating ${name}."
    detector_id=""
  fi

  if [ -z "${detector_id}" ]; then
    payload="$(cat <<JSON
{
  "name": "${name}",
  "description": "Thesis-lab detector for ${metric_type} usage, modelled independently for every host_id",
  "time_field": "@timestamp",
  "indices": ["metrics-*"],
  "feature_attributes": [
    {
      "feature_name": "${feature_name}",
      "feature_enabled": true,
      "aggregation_query": {
        "${feature_name}": {
          "avg": {"field": "${metric_field}"}
        }
      }
    }
  ],
  "filter_query": {
    "bool": {
      "filter": [
        {"term": {"metric_type": "${metric_type}"}},
        {"exists": {"field": "${metric_field}"}}
      ]
    }
  },
  "detection_interval": {"period": {"interval": 1, "unit": "Minutes"}},
  "window_delay": {"period": {"interval": 1, "unit": "Minutes"}},
  "shingle_size": 8,
  "schema_version": 0,
  "category_field": ["host_id"]
}
JSON
)"

    response="$(curl -fsS -X POST \
      "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
      -H "Content-Type: application/json" \
      -d "${payload}")"
    detector_id="$(printf '%s' "${response}" | json_string_value _id)"
    if [ -z "${detector_id}" ]; then
      echo "OpenSearch created no detector ID for ${name}. Response: ${response}" >&2
      return 1
    fi

    curl -fsS -X PUT \
      "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}?refresh=true" \
      -H "Content-Type: application/json" \
      -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${name}\",\"metric_type\":\"${metric_type}\",\"metric_field\":\"${metric_field}\"}" \
      >/dev/null
    echo "Created detector ${name} (${detector_id})."
  else
    echo "Reusing detector ${name} (${detector_id})."
  fi

  start_detector "${detector_id}" "${name}"
}

wait_for_plugin
wait_for_metric cpu usage_active
wait_for_metric memory used_percent
create_config_index

ensure_detector \
  cpu-usage-active-v1 \
  thesis-cpu-usage-active \
  cpu \
  usage_active \
  average_cpu_usage_active

ensure_detector \
  memory-used-percent-v1 \
  thesis-memory-used-percent \
  memory \
  used_percent \
  average_memory_used_percent

echo "CPU and memory anomaly detectors are provisioned."
