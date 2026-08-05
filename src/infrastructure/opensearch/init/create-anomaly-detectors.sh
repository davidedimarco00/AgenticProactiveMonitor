#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
CONFIG_INDEX="agentic-detector-config"
DETECTOR_KEY="cpu-native-telegraf-v1"
DETECTOR_NAME="thesis-cpu-anomaly-detector"
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
    -d '{
      "settings": {"number_of_shards": 1, "number_of_replicas": 0},
      "mappings": {
        "properties": {
          "detector_id": {"type": "keyword"},
          "name": {"type": "keyword"},
          "source_field": {"type": "keyword"},
          "category_field": {"type": "keyword"},
          "status": {"type": "keyword"}
        }
      }
    }' || true)"

  case "${status}" in
    200|201) echo "Created ${CONFIG_INDEX}." ;;
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

cpu_document_count() {
  status="$(curl -sS -o /tmp/cpu-count.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/metrics-*/_count" \
    -H "Content-Type: application/json" \
    -d '{
      "query": {
        "bool": {
          "filter": [
            {"term": {"measurement_name": "cpu"}},
            {"exists": {"field": "cpu.usage_active"}},
            {"exists": {"field": "tag.host_id"}}
          ]
        }
      }
    }' || true)"

  if [ "${status}" != "200" ]; then
    echo 0
    return 0
  fi

  sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    /tmp/cpu-count.json | head -n 1
}

wait_for_cpu_documents() {
  elapsed=0
  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    count="$(cpu_document_count)"
    count="${count:-0}"
    if [ "${count}" -gt 0 ]; then
      echo "Found ${count} CPU documents with cpu.usage_active and tag.host_id."
      return 0
    fi

    echo "Waiting for Telegraf CPU documents (cpu.usage_active)..."
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "No valid CPU telemetry arrived within ${WAIT_SECONDS}s." >&2
  echo "Expected document structure: measurement_name=cpu, cpu.usage_active, tag.host_id." >&2
  curl -sS -X POST "${OPENSEARCH_URL}/metrics-*/_search?pretty" \
    -H "Content-Type: application/json" \
    -d '{"size":3,"sort":[{"@timestamp":"desc"}]}' >&2 || true
  return 1
}

validate_source_mapping() {
  curl -fsS \
    "${OPENSEARCH_URL}/metrics-*/_field_caps?fields=@timestamp,measurement_name,cpu.usage_active,tag.host_id" \
    >/tmp/cpu-field-caps.json

  for field in '@timestamp' 'measurement_name' 'cpu.usage_active' 'tag.host_id'; do
    if ! grep -q "\"${field}\"" /tmp/cpu-field-caps.json; then
      echo "Required field ${field} is missing from metrics-* mappings." >&2
      cat /tmp/cpu-field-caps.json >&2
      return 1
    fi
  done

  if ! grep -q '"cpu.usage_active"' /tmp/cpu-field-caps.json || \
     ! grep -Eq '"(float|double)"' /tmp/cpu-field-caps.json; then
    echo "cpu.usage_active is not mapped as a numeric field." >&2
    cat /tmp/cpu-field-caps.json >&2
    return 1
  fi

  echo "CPU source mapping is valid."
}

stored_detector_id() {
  status="$(curl -sS -o /tmp/stored-detector.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${DETECTOR_KEY}" || true)"
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

create_detector() {
  cat >/tmp/cpu-detector.json <<'JSON'
{
  "name": "thesis-cpu-anomaly-detector",
  "description": "Real-time CPU anomaly detector for the five thesis-lab machines using native nested Telegraf fields",
  "time_field": "@timestamp",
  "indices": ["metrics-*"],
  "feature_attributes": [
    {
      "feature_name": "average_cpu_usage_active",
      "feature_enabled": true,
      "aggregation_query": {
        "average_cpu_usage_active": {
          "avg": {"field": "cpu.usage_active"}
        }
      }
    }
  ],
  "filter_query": {
    "bool": {
      "filter": [
        {"term": {"measurement_name": "cpu"}},
        {"exists": {"field": "cpu.usage_active"}},
        {"exists": {"field": "tag.host_id"}}
      ]
    }
  },
  "detection_interval": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "window_delay": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "category_field": ["tag.host_id"]
}
JSON

  validation_status="$(curl -sS -o /tmp/cpu-detector-validation.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_validate/detector" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/cpu-detector.json || true)"

  if [ "${validation_status}" != "200" ]; then
    echo "CPU detector validation failed (HTTP ${validation_status})." >&2
    cat /tmp/cpu-detector-validation.json >&2 || true
    return 1
  fi

  validation_body="$(tr -d '[:space:]' </tmp/cpu-detector-validation.json)"
  if [ -n "${validation_body}" ] && [ "${validation_body}" != "{}" ]; then
    echo "CPU detector validation response:"
    cat /tmp/cpu-detector-validation.json
  fi

  status="$(curl -sS -o /tmp/cpu-detector-create.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/cpu-detector.json || true)"

  case "${status}" in
    200|201) ;;
    *)
      echo "Unable to create ${DETECTOR_NAME} (HTTP ${status})." >&2
      cat /tmp/cpu-detector-create.json >&2 || true
      return 1
      ;;
  esac

  detector_id="$(json_string_value _id </tmp/cpu-detector-create.json)"
  if [ -z "${detector_id}" ]; then
    echo "OpenSearch did not return a detector ID." >&2
    cat /tmp/cpu-detector-create.json >&2 || true
    return 1
  fi

  curl -fsS -X PUT \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${DETECTOR_KEY}?refresh=true" \
    -H "Content-Type: application/json" \
    -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${DETECTOR_NAME}\",\"source_field\":\"cpu.usage_active\",\"category_field\":\"tag.host_id\",\"status\":\"created\"}" \
    >/dev/null

  echo "Created ${DETECTOR_NAME} (${detector_id})."
  printf '%s' "${detector_id}"
}

start_detector() {
  detector_id="$1"
  status="$(curl -sS -o /tmp/cpu-detector-start.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" || true)"

  case "${status}" in
    200|201)
      echo "Started ${DETECTOR_NAME} (${detector_id})."
      ;;
    400)
      if grep -Eqi 'already|running|scheduled|enabled' /tmp/cpu-detector-start.json; then
        echo "${DETECTOR_NAME} is already running or scheduled."
      else
        echo "Unable to start ${DETECTOR_NAME} (HTTP 400)." >&2
        cat /tmp/cpu-detector-start.json >&2 || true
        return 1
      fi
      ;;
    *)
      echo "Unable to start ${DETECTOR_NAME} (HTTP ${status})." >&2
      cat /tmp/cpu-detector-start.json >&2 || true
      return 1
      ;;
  esac
}

wait_for_plugin
create_config_index
wait_for_cpu_documents
validate_source_mapping

detector_id="$(stored_detector_id || true)"
if [ -n "${detector_id}" ] && detector_exists "${detector_id}"; then
  echo "Reusing ${DETECTOR_NAME} (${detector_id})."
else
  if [ -n "${detector_id}" ]; then
    echo "Stored detector ${detector_id} no longer exists; recreating it."
  fi
  detector_id="$(create_detector | tee /tmp/cpu-detector-create.log | tail -n 1)"
fi

if [ -z "${detector_id}" ]; then
  echo "Unable to resolve the CPU detector ID." >&2
  cat /tmp/cpu-detector-create.log >&2 2>/dev/null || true
  exit 1
fi

start_detector "${detector_id}"
echo "CPU anomaly detector provisioning completed successfully."
