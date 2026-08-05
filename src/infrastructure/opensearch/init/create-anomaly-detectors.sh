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
    -d '{"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"properties":{"detector_id":{"type":"keyword"},"name":{"type":"keyword"},"measurement":{"type":"keyword"},"metric_field":{"type":"keyword"}}}}' || true)"

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
      echo "Detector ${name} is already running or already scheduled."
      ;;
    *)
      echo "Unable to start detector ${name} (HTTP ${status})." >&2
      cat /tmp/detector-start.json >&2 || true
      return 1
      ;;
  esac
}

ensure_detector() {
  key="$1"
  name="$2"
  measurement="$3"
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
  "description": "Thesis-lab detector for ${measurement}.${metric_field}, modelled independently for every host_id",
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
        {"term": {"name": "${measurement}"}},
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

    status="$(curl -sS -o /tmp/detector-create.json -w "%{http_code}" \
      -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
      -H "Content-Type: application/json" \
      -d "${payload}" || true)"

    case "${status}" in
      200|201) ;;
      *)
        echo "Unable to create detector ${name} (HTTP ${status})." >&2
        cat /tmp/detector-create.json >&2 || true
        return 1
        ;;
    esac

    detector_id="$(json_string_value _id </tmp/detector-create.json)"
    if [ -z "${detector_id}" ]; then
      echo "OpenSearch created no detector ID for ${name}." >&2
      cat /tmp/detector-create.json >&2 || true
      return 1
    fi

    curl -fsS -X PUT \
      "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}?refresh=true" \
      -H "Content-Type: application/json" \
      -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${name}\",\"measurement\":\"${measurement}\",\"metric_field\":\"${metric_field}\"}" \
      >/dev/null

    echo "Created detector ${name} (${detector_id})."
  else
    echo "Reusing detector ${name} (${detector_id})."
  fi

  start_detector "${detector_id}" "${name}"
}

# The metrics-bootstrap index is created from the metrics template before this
# script runs. Therefore the required field mappings already exist and detector
# creation does not need to wait for the first Telegraf samples.
wait_for_plugin
create_config_index

ensure_detector \
  cpu-usage-active-v2 \
  thesis-cpu-usage-active \
  cpu \
  usage_active \
  average_cpu_usage_active

ensure_detector \
  memory-used-percent-v2 \
  thesis-memory-used-percent \
  mem \
  used_percent \
  average_memory_used_percent

echo "CPU and memory anomaly detectors are provisioned."
