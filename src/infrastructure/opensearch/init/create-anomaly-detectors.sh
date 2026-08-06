#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:-}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:-}"
CONFIG_INDEX="agentic-detector-config"
WAIT_SECONDS="${DETECTOR_WAIT_SECONDS:-180}"
MIN_DOCUMENTS="${DETECTOR_MIN_DOCUMENTS:-5}"

CPU_DETECTOR_KEY="cpu-single-entity-v1"
CPU_DETECTOR_NAME="thesis-cpu-anomaly-detector"
MEMORY_DETECTOR_KEY="memory-single-entity-v1"
MEMORY_DETECTOR_NAME="thesis-memory-anomaly-detector"

os_curl() {
  if [ -n "${OPENSEARCH_USERNAME}" ]; then
    curl -k -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" "$@"
  else
    curl -k "$@"
  fi
}

json_string_value() {
  key="$1"
  sed -n "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

wait_for_plugin() {
  elapsed=0
  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    status="$(os_curl -sS -o /tmp/ad-stats.json -w "%{http_code}" \
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
  status="$(os_curl -sS -o /tmp/config-index.json -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${CONFIG_INDEX}" \
    -H "Content-Type: application/json" \
    -d '{
      "settings": {"number_of_shards": 1, "number_of_replicas": 0},
      "mappings": {
        "properties": {
          "detector_id": {"type": "keyword"},
          "name": {"type": "keyword"},
          "measurement_name": {"type": "keyword"},
          "source_field": {"type": "keyword"},
          "detector_type": {"type": "keyword"},
          "status": {"type": "keyword"},
          "shingle_size": {"type": "integer"}
        }
      }
    }' || true)"

  case "$status" in
    200|201)
      echo "Created ${CONFIG_INDEX}."
      ;;
    400)
      if grep -q resource_already_exists_exception /tmp/config-index.json; then
        echo "${CONFIG_INDEX} already exists."
      else
        cat /tmp/config-index.json >&2
        return 1
      fi
      ;;
    *)
      cat /tmp/config-index.json >&2 || true
      return 1
      ;;
  esac
}

telemetry_document_count() {
  measurement="$1"
  metric_type="$2"
  source_field="$3"

  status="$(os_curl -sS -o /tmp/telemetry-count.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/metrics-*/_count" \
    -H "Content-Type: application/json" \
    -d "{\"query\":{\"bool\":{\"filter\":[{\"term\":{\"measurement_name\":\"${measurement}\"}},{\"term\":{\"tag.metric_type\":\"${metric_type}\"}},{\"exists\":{\"field\":\"${source_field}\"}}]}}}" || true)"

  if [ "$status" != "200" ]; then
    echo 0
    return
  fi

  sed -n 's/.*"count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    /tmp/telemetry-count.json | head -n 1
}

wait_for_telemetry() {
  label="$1"
  measurement="$2"
  metric_type="$3"
  source_field="$4"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    count="$(telemetry_document_count "$measurement" "$metric_type" "$source_field")"
    count="${count:-0}"

    if [ "$count" -ge "$MIN_DOCUMENTS" ]; then
      echo "Found ${count} valid ${label} documents."
      return 0
    fi

    echo "Waiting for ${label} telemetry (${count}/${MIN_DOCUMENTS} documents)..."
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "Not enough ${label} telemetry arrived within ${WAIT_SECONDS}s." >&2
  return 1
}

stored_detector_id() {
  key="$1"
  status="$(os_curl -sS -o /tmp/stored-detector.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}" || true)"

  if [ "$status" = "200" ]; then
    json_string_value detector_id </tmp/stored-detector.json
  fi
}

detector_exists() {
  detector_id="$1"
  status="$(os_curl -sS -o /tmp/detector-get.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" || true)"
  [ "$status" = "200" ]
}

remove_detector_by_key() {
  key="$1"
  label="$2"
  detector_id="$(stored_detector_id "$key" || true)"

  [ -n "$detector_id" ] || return 0

  os_curl -sS -X POST \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_stop" \
    >/dev/null 2>&1 || true

  status="$(os_curl -sS -o /tmp/detector-delete.json -w "%{http_code}" \
    -X DELETE "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}" || true)"

  case "$status" in
    200|404)
      echo "Removed ${label} detector ${detector_id}."
      ;;
    *)
      echo "Unable to remove detector ${detector_id} (HTTP ${status})." >&2
      cat /tmp/detector-delete.json >&2 || true
      return 1
      ;;
  esac

  os_curl -sS -X DELETE \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}?refresh=true" \
    >/dev/null 2>&1 || true
}

create_detector() {
  key="$1"
  name="$2"
  description="$3"
  measurement="$4"
  source_field="$5"
  feature_name="$6"

  cat >/tmp/detector.json <<JSON
{
  "name": "${name}",
  "description": "${description}",
  "time_field": "@timestamp",
  "indices": ["metrics-*"],
  "shingle_size": 4,
  "schema_version": 0,
  "feature_attributes": [
    {
      "feature_name": "${feature_name}",
      "feature_enabled": true,
      "aggregation_query": {
        "${feature_name}": {
          "avg": {"field": "${source_field}"}
        }
      }
    }
  ],
  "filter_query": {
    "match_all": {"boost": 1.0}
  },
  "detection_interval": {
    "period": {"interval": 1, "unit": "Minutes"}
  },
  "window_delay": {
    "period": {"interval": 1, "unit": "Minutes"}
  }
}
JSON

  validation_status="$(os_curl -sS -o /tmp/detector-validation.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/_validate/detector" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json || true)"

  if [ "$validation_status" != "200" ]; then
    echo "Detector validation failed for ${name} (HTTP ${validation_status})." >&2
    cat /tmp/detector-validation.json >&2 || true
    return 1
  fi

  status="$(os_curl -sS -o /tmp/detector-create.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors" \
    -H "Content-Type: application/json" \
    --data-binary @/tmp/detector.json || true)"

  case "$status" in
    200|201)
      ;;
    *)
      echo "Unable to create ${name} (HTTP ${status})." >&2
      cat /tmp/detector-create.json >&2 || true
      return 1
      ;;
  esac

  detector_id="$(json_string_value _id </tmp/detector-create.json)"
  [ -n "$detector_id" ] || {
    cat /tmp/detector-create.json >&2
    return 1
  }

  os_curl -fsS -X PUT \
    "${OPENSEARCH_URL}/${CONFIG_INDEX}/_doc/${key}?refresh=true" \
    -H "Content-Type: application/json" \
    -d "{\"detector_id\":\"${detector_id}\",\"name\":\"${name}\",\"measurement_name\":\"${measurement}\",\"source_field\":\"${source_field}\",\"detector_type\":\"SINGLE_ENTITY\",\"status\":\"created\",\"shingle_size\":4}" \
    >/dev/null

  printf '%s' "$detector_id"
}

start_detector() {
  detector_id="$1"
  name="$2"

  status="$(os_curl -sS -o /tmp/detector-start.json -w "%{http_code}" \
    -X POST "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_start" || true)"

  case "$status" in
    200|201)
      echo "Started ${name} (${detector_id})."
      ;;
    400)
      if grep -Eqi 'already|running|scheduled|enabled' /tmp/detector-start.json; then
        echo "${name} is already running or scheduled."
      else
        cat /tmp/detector-start.json >&2
        return 1
      fi
      ;;
    *)
      cat /tmp/detector-start.json >&2 || true
      return 1
      ;;
  esac
}

show_profile() {
  detector_id="$1"
  name="$2"

  sleep 3
  status="$(os_curl -sS -o /tmp/detector-profile.json -w "%{http_code}" \
    "${OPENSEARCH_URL}/_plugins/_anomaly_detection/detectors/${detector_id}/_profile?_all=true&pretty" || true)"

  if [ "$status" = "200" ]; then
    echo "Initial profile for ${name}:"
    cat /tmp/detector-profile.json
  else
    echo "Detector profile for ${name} is not available yet (HTTP ${status})."
  fi
}

provision_detector() {
  key="$1"
  name="$2"
  description="$3"
  measurement="$4"
  source_field="$5"
  feature_name="$6"

  detector_id="$(stored_detector_id "$key" || true)"

  if [ -n "$detector_id" ] && detector_exists "$detector_id"; then
    echo "Reusing ${name} (${detector_id})."
  else
    detector_id="$(create_detector \
      "$key" "$name" "$description" "$measurement" "$source_field" "$feature_name")"
    echo "Created ${name} (${detector_id}) as SINGLE_ENTITY with shingle size 4."
  fi

  start_detector "$detector_id" "$name"
  show_profile "$detector_id" "$name"
}

wait_for_plugin
create_config_index
wait_for_telemetry "CPU" "cpu" "cpu" "cpu.usage_active"
wait_for_telemetry "memory" "mem" "memory" "mem.used_percent"

# Remove all detector definitions from the previous HCAD revisions. The
# category field cannot be changed after detector creation, so they must be
# deleted and recreated as single-entity detectors.
remove_detector_by_key "cpu-native-telegraf-v1" "legacy CPU"
remove_detector_by_key "cpu-native-telegraf-v2" "legacy CPU"
remove_detector_by_key "cpu-native-telegraf-v3" "HCAD CPU"
remove_detector_by_key "memory-native-telegraf-v1" "HCAD memory"

provision_detector \
  "$CPU_DETECTOR_KEY" \
  "$CPU_DETECTOR_NAME" \
  "Fast single-entity CPU detector based on the validated laboratory configuration" \
  "cpu" \
  "cpu.usage_active" \
  "avg_cpu_used_percent"

provision_detector \
  "$MEMORY_DETECTOR_KEY" \
  "$MEMORY_DETECTOR_NAME" \
  "Fast single-entity memory detector based on the validated laboratory configuration" \
  "mem" \
  "mem.used_percent" \
  "avg_memory_used_percent"

echo "CPU and memory SINGLE_ENTITY detector provisioning completed successfully."
