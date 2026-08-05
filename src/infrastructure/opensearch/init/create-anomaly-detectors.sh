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
      "${OPENSEARCH_URL}/_plugins/_anomaly_detection/stats" || true)"
    if [ "${status}" = "200" ]; then
      echo "OpenSearch Anomaly Detection plugin is available."
      return 0
    fi
    echo "Waiting for the OpenSearch Anomaly Detection plugin..."
    sleep 3
    elapsed=$((elapsed + 3))
  done

  echo "Anomaly Detection plugin did not become available." >&2
  cat /tmp/ad-stats.json >&2 || true
  return 1
}

create_config_index() {
  status="$(curl -sS -o /tmp/config-index.json -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${CONFIG_INDEX}" \
    -H "Content-Type: application/json" \
    -d '{"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"properties":{"detector_id":{"type":"keyword"},"name":{"type":"keyword"},"metric_field":{"type":"keyword"},"detector_mode":{"type":"keyword"}}}}' || true)"

  case "${status}" in
    200|201)
      echo "Created ${CONFIG_INDEX}."
      ;;
    400)
      if grep -q 'resource_already_exists_exception' /tmp/config-index.json; then
        echo "${CONFIG_INDEX} already exists."
      else
        echo "Unable to create ${CONFIG_INDEX} (HTTP 400)." >&2
        cat /tmp/config-index.json >&2
        return 1
      fi
      ;;
    *)
      echo "Unable to create ${CONFIG_INDEX} (HTTP ${status})." >&2
      cat /tmp/config-index.json >&2 || true
      return 1
      ;;
  esac
}

metric_count() {
  filters="$1"
  status="$(curl -sS -o /tmp/metric-count.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/metrics-*/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":${filters}}}}" || true)"

  if [ "${status}" != "200" ]; then
    echo 0
    return 0
  fi

  sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    /tmp/metric-count.json | head -n 1
}

wait_for_metric_data() {
  label="$1"
  filters="$2"
  elapsed=0

  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    count="$(metric_count "${filters}")"
    count="${count:-0}"
    if [ "${count}" -gt 0 ]; then
      echo "Found ${count} real ${label} metric documents."
      return 0
    fi

    echo "Waiting for real ${label} telemetry..."
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "No ${label} telemetry arrived within ${WAIT_SECONDS}s." >&2
  echo "Inspect the monitored-machine Telegraf logs and metrics-* documents." >&2
  return 1
}

stored_detector_id() {
  key="$1"
  status="$(curl -sS -o /tmp/stored-detector.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}" || true)"
  if [ "${status}" = "200" ]; then
    json_string_value detector_id </tmp/stored-detector.json
  fi
}

detector_exists() {
  detector_id="$1"
  status="$(curl -sS -o /tmp/detector-get.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" || true)"
  [ "${status}" = "200" ]
}

start_detector() {
  detector_id="$1"
  name="$2"
  status="$(curl -sS -o /tmp/detector-start.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" || true)"

  case "${status}" in
    200|201)
      echo "Started detector ${name} (${detector_id})."
      ;;
    400)
      if grep -Eqi 'already|running|scheduled|enabled' /tmp/detector-start.json; then
        echo "Detector ${name} is already running or scheduled."
      else
        echo "Unable to start detector ${name} (HTTP 400)." >&2
        cat /tmp/detector-start.json >&2 || true
        return 1
      fi
      ;;
    *)
      echo "Unable to start detector ${name} (HTTP ${status})." >&2
      cat /tmp/detector-start.json >&2 || true
      return 1
      ;;
  esac
}

build_detector_payload() {
  name="$1"
  metric_field="$2"
  feature_name="$3"
  filters="$4"
  mode="$5"

  category=""
  description="Thesis-lab detector for ${metric_field}"
  if [ "${mode}" = "multi_entity" ]; then
    category=',
  "category_field": ["host_id"]'
    description="${description}, modelled independently for every host_id"
  else
    description="${description}, aggregated across the monitored infrastructure"
  fi

  cat >/tmp/detector-payload.json <<JSON
{
  "name": "${name}",
  "description": "${description}",
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
      "filter": ${filters},
      "adjust_pure_negative": true,
      "boost": 1
    }
  },
  "detection_interval": {"period": {"interval": 1, "unit": "Minutes"}},
  "window_delay": {"period": {"interval": 1, "unit": "Minutes"}}${category}
}
JSON
}

validate_detector() {
  name="$1"
  status="$(curl -sS -o /tmp/detector-validate.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_validate/detector" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector-payload.json || true)"

  if [ "${status}" != "200" ]; then
    echo "Validation request for ${name} returned HTTP ${status}:" >&2
    cat /tmp/detector-validate.json >&2 || true
    return 1
  fi

  compact="$(tr -d '[:space:]' </tmp/detector-validate.json)"
  if [ -n "${compact}" ] && [ "${compact}" != "{}" ]; then
    echo "OpenSearch validation report for ${name}:" >&2
    cat /tmp/detector-validate.json >&2
  fi
}

create_detector() {
  name="$1"
  status="$(curl -sS -o /tmp/detector-create.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector-payload.json || true)"

  case "${status}" in
    200|201) return 0 ;;
    *)
      echo "Unable to create detector ${name} (HTTP ${status})." >&2
      cat /tmp/detector-create.json >&2 || true
      return 1
      ;;
  esac
}

ensure_detector() {
  key="$1"
  name="$2"
  metric_field="$3"
  feature_name="$4"
  filters="$5"

  detector_id="$(stored_detector_id "${key}" || true)"
  if [ -n "${detector_id}" ] && ! detector_exists "${detector_id}"; then
    echo "Stored detector ${detector_id} no longer exists; recreating ${name}."
    detector_id=""
  fi

  if [ -z "${detector_id}" ]; then
    detector_mode="multi_entity"
    build_detector_payload \
      "${name}" "${metric_field}" "${feature_name}" "${filters}" "${detector_mode}"
    validate_detector "${name}" || true

    if ! create_detector "${name}"; then
      echo "Retrying ${name} as a single-entity detector." >&2
      detector_mode="single_entity"
      build_detector_payload \
        "${name}" "${metric_field}" "${feature_name}" "${filters}" "${detector_mode}"
      validate_detector "${name}" || true
      create_detector "${name}"
    fi

    detector_id="$(json_string_value _id </tmp/detector-create.json)"
    if [ -z "${detector_id}" ]; then
      echo "OpenSearch created no detector ID for ${name}." >&2
      cat /tmp/detector-create.json >&2 || true
      return 1
    fi

    curl -fsS -X PUT \
      "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}?refresh=true" \
      -H "Content-Type: application/json" \
      -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${name}\",\"metric_field\":\"${metric_field}\",\"detector_mode\":\"${detector_mode}\"}" \
      >/dev/null

    echo "Created detector ${name} (${detector_id}) in ${detector_mode} mode."
  else
    echo "Reusing detector ${name} (${detector_id})."
  fi

  start_detector "${detector_id}" "${name}"
}

CPU_FILTERS='[{"exists":{"field":"usage_active"}},{"exists":{"field":"cpu"}}]'
MEMORY_FILTERS='[{"exists":{"field":"used_percent"}},{"exists":{"field":"available_percent"}},{"exists":{"field":"total"}}]'

wait_for_plugin
create_config_index

# Detector creation validates the feature aggregation against source data. Wait
# for actual Telegraf documents, but identify them through their native fields
# instead of optional custom tags or measurement-name assumptions.
wait_for_metric_data "CPU usage_active" "${CPU_FILTERS}"
wait_for_metric_data "memory used_percent" "${MEMORY_FILTERS}"

ensure_detector \
  cpu-usage-active-v3 \
  thesis-cpu-usage-active \
  usage_active \
  average_cpu_usage_active \
  "${CPU_FILTERS}"

ensure_detector \
  memory-used-percent-v3 \
  thesis-memory-used-percent \
  used_percent \
  average_memory_used_percent \
  "${MEMORY_FILTERS}"

echo "CPU and memory anomaly detectors are provisioned."
