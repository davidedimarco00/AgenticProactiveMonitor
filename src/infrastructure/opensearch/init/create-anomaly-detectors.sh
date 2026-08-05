#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
CONFIG_INDEX="agentic-detector-config"
OLD_DETECTOR_KEY="cpu-native-telegraf-v1"
DETECTOR_KEY="cpu-native-telegraf-v2"
DETECTOR_NAME="thesis-cpu-anomaly-detector"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-180}"

json_string_value() {
  key="$1"
  sed -n "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

wait_for_plugin() {
  elapsed=0
  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    status="$(curl -sS -o /tmp/ad-stats.json -w "%{http_code}" \
      "${OPENSEARCH_URL}/_plugins/_anomaly_detection/stats" || true)"
    if [ "$status" = "200" ]; then
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
    -d '{"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"properties":{"detector_id":{"type":"keyword"},"name":{"type":"keyword"},"source_field":{"type":"keyword"},"category_field":{"type":"keyword"},"status":{"type":"keyword"},"shingle_size":{"type":"integer"}}}}' || true)"
  case "$status" in
    200|201) echo "Created ${CONFIG_INDEX}." ;;
    400)
      if grep -q resource_already_exists_exception /tmp/config-index.json; then
        echo "${CONFIG_INDEX} already exists."
      else
        cat /tmp/config-index.json >&2
        return 1
      fi
      ;;
    *) cat /tmp/config-index.json >&2 || true; return 1 ;;
  esac
}

enable_fast_hcad_cold_start() {
  status="$(curl -sS -o /tmp/ad-settings.json -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/_cluster/settings" \
    -H "Content-Type: application/json" \
    -d '{"persistent":{"plugins.anomaly_detection.hcad_cold_start_interpolation.enabled":true}}' || true)"
  if [ "$status" = "200" ]; then
    echo "Enabled HCAD cold-start interpolation for the thesis lab."
  else
    echo "Unable to enable HCAD cold-start interpolation (HTTP ${status})." >&2
    cat /tmp/ad-settings.json >&2 || true
    return 1
  fi
}

cpu_document_count() {
  status="$(curl -sS -o /tmp/cpu-count.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/metrics-*/_count" \
    -H "Content-Type: application/json" \
    -d '{"query":{"bool":{"filter":[{"term":{"measurement_name":"cpu"}},{"exists":{"field":"cpu.usage_active"}},{"exists":{"field":"tag.host_id"}}]}}}' || true)"
  if [ "$status" != "200" ]; then echo 0; return; fi
  sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' /tmp/cpu-count.json | head -n 1
}

wait_for_cpu_documents() {
  elapsed=0
  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    count="$(cpu_document_count)"
    count="${count:-0}"
    if [ "$count" -ge 20 ]; then
      echo "Found ${count} valid CPU documents."
      return 0
    fi
    echo "Waiting for CPU telemetry (${count}/20 documents)..."
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo "Not enough CPU telemetry arrived within ${WAIT_SECONDS}s." >&2
  return 1
}

stored_detector_id() {
  key="$1"
  status="$(curl -sS -o /tmp/stored-detector.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}" || true)"
  if [ "$status" = "200" ]; then
    json_string_value detector_id </tmp/stored-detector.json
  fi
}

detector_exists() {
  detector_id="$1"
  status="$(curl -sS -o /tmp/detector-get.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" || true)"
  [ "$status" = "200" ]
}

remove_detector() {
  detector_id="$1"
  label="$2"
  [ -n "$detector_id" ] || return 0
  curl -sS -X POST \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop" \
    >/dev/null 2>&1 || true
  status="$(curl -sS -o /tmp/detector-delete.json -w "%{http_code}" \
    -X DELETE "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" || true)"
  case "$status" in
    200|404) echo "Removed ${label} detector ${detector_id}." ;;
    *)
      echo "Unable to remove detector ${detector_id} (HTTP ${status})." >&2
      cat /tmp/detector-delete.json >&2 || true
      return 1
      ;;
  esac
}

migrate_old_detector() {
  old_id="$(stored_detector_id "$OLD_DETECTOR_KEY" || true)"
  if [ -n "$old_id" ]; then
    remove_detector "$old_id" "old initializing"
    curl -sS -X DELETE \
      "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${OLD_DETECTOR_KEY}?refresh=true" \
      >/dev/null 2>&1 || true
  fi
}

create_detector() {
  cat >/tmp/cpu-detector.json <<'JSON'
{
  "name": "thesis-cpu-anomaly-detector",
  "description": "Fast cold-start CPU anomaly detector for the five thesis-lab machines",
  "time_field": "@timestamp",
  "indices": ["metrics-*"],
  "shingle_size": 4,
  "schema_version": 0,
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
  if [ "$validation_status" != "200" ]; then
    echo "CPU detector validation failed (HTTP ${validation_status})." >&2
    cat /tmp/cpu-detector-validation.json >&2 || true
    return 1
  fi

  status="$(curl -sS -o /tmp/cpu-detector-create.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/cpu-detector.json || true)"
  case "$status" in
    200|201) ;;
    *)
      echo "Unable to create ${DETECTOR_NAME} (HTTP ${status})." >&2
      cat /tmp/cpu-detector-create.json >&2 || true
      return 1
      ;;
  esac

  detector_id="$(json_string_value _id </tmp/cpu-detector-create.json)"
  [ -n "$detector_id" ] || { cat /tmp/cpu-detector-create.json >&2; return 1; }

  curl -fsS -X PUT \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${DETECTOR_KEY}?refresh=true" \
    -H "Content-Type: application/json" \
    -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${DETECTOR_NAME}\",\"source_field\":\"cpu.usage_active\",\"category_field\":\"tag.host_id\",\"status\":\"created\",\"shingle_size\":4}" \
    >/dev/null

  printf '%s' "$detector_id"
}

start_detector() {
  detector_id="$1"
  status="$(curl -sS -o /tmp/cpu-detector-start.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" || true)"
  case "$status" in
    200|201) echo "Started ${DETECTOR_NAME} (${detector_id})." ;;
    400)
      if grep -Eqi 'already|running|scheduled|enabled' /tmp/cpu-detector-start.json; then
        echo "${DETECTOR_NAME} is already running or scheduled."
      else
        cat /tmp/cpu-detector-start.json >&2
        return 1
      fi
      ;;
    *) cat /tmp/cpu-detector-start.json >&2 || true; return 1 ;;
  esac
}

show_profile() {
  detector_id="$1"
  sleep 3
  status="$(curl -sS -o /tmp/cpu-detector-profile.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_profile?_all=true&pretty" || true)"
  if [ "$status" = "200" ]; then
    echo "Initial detector profile:"
    cat /tmp/cpu-detector-profile.json
  else
    echo "Detector profile is not available yet (HTTP ${status})."
  fi
}

wait_for_plugin
create_config_index
enable_fast_hcad_cold_start
wait_for_cpu_documents
migrate_old_detector

detector_id="$(stored_detector_id "$DETECTOR_KEY" || true)"
if [ -n "$detector_id" ] && detector_exists "$detector_id"; then
  echo "Reusing ${DETECTOR_NAME} (${detector_id})."
else
  detector_id="$(create_detector)"
  echo "Created ${DETECTOR_NAME} (${detector_id}) with shingle size 4."
fi

start_detector "$detector_id"
show_profile "$detector_id"
echo "CPU anomaly detector provisioning completed successfully."
