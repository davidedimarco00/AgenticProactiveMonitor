#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:-}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:-}"

os_curl() {
  if [ -n "${OPENSEARCH_USERNAME}" ]; then
    curl -k -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" "$@"
  else
    curl -k "$@"
  fi
}

echo "Waiting for OpenSearch..."
until os_curl -fsS "${OPENSEARCH_URL}/_cluster/health" >/dev/null; do
  echo "OpenSearch is not ready yet..."
  sleep 3
done

echo "OpenSearch is available."

echo "Creating metrics index template for the native Telegraf OpenSearch format..."
os_curl -fsS -X PUT "${OPENSEARCH_URL}/_index_template/metrics-template" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["metrics-*"],
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
      },
      "mappings": {
        "dynamic": true,
        "dynamic_templates": [
          {
            "telegraf_tags_as_keywords": {
              "path_match": "tag.*",
              "match_mapping_type": "string",
              "mapping": {
                "type": "keyword",
                "ignore_above": 256
              }
            }
          }
        ],
        "properties": {
          "@timestamp": {"type": "date"},
          "measurement_name": {"type": "keyword"},
          "tag": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "host": {"type": "keyword"},
              "host_id": {"type": "keyword"},
              "machine_role": {"type": "keyword"},
              "metric_type": {"type": "keyword"},
              "cpu": {"type": "keyword"},
              "environment": {"type": "keyword"},
              "project": {"type": "keyword"},
              "monitored_by": {"type": "keyword"}
            }
          },
          "cpu": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "usage_active": {"type": "float"}
            }
          },
          "mem": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "used_percent": {"type": "float"}
            }
          },
          "disk": {"type": "object", "dynamic": true},
          "diskio": {"type": "object", "dynamic": true},
          "net": {"type": "object", "dynamic": true},
          "system": {"type": "object", "dynamic": true},
          "swap": {"type": "object", "dynamic": true},
          "processes": {"type": "object", "dynamic": true},
          "kernel": {"type": "object", "dynamic": true}
        }
      }
    },
    "priority": 200,
    "version": 3,
    "_meta": {
      "description": "Complete native Telegraf metrics mapping for the thesis monitoring lab"
    }
  }' >/dev/null

echo "Metrics index template created."

echo "Creating logs index template..."
os_curl -fsS -X PUT "${OPENSEARCH_URL}/_index_template/logs-template" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-*"],
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
      },
      "mappings": {
        "dynamic": true,
        "properties": {
          "@timestamp": {"type": "date"},
          "timestamp": {"type": "date"},
          "host": {"type": "keyword"},
          "host_id": {"type": "keyword"},
          "machine_role": {"type": "keyword"},
          "environment": {"type": "keyword"},
          "project": {"type": "keyword"},
          "monitored_by": {"type": "keyword"},
          "service": {"type": "keyword"},
          "component": {"type": "keyword"},
          "event_type": {"type": "keyword"},
          "level": {"type": "keyword"},
          "message": {"type": "text"},
          "latency_ms": {"type": "float"},
          "cpu_signal": {"type": "float"},
          "memory_signal": {"type": "float"},
          "requests": {"type": "integer"},
          "error_code": {"type": "integer"},
          "uptime_seconds": {"type": "long"},
          "load_average": {"type": "float"}
        }
      }
    },
    "priority": 100,
    "version": 2,
    "_meta": {
      "description": "Fluent Bit application and system log mapping for the thesis monitoring lab"
    }
  }' >/dev/null

echo "Logs index template created."

# Older versions of this project mapped the top-level cpu property as a
# keyword. Telegraf writes cpu as an object, so those indexes reject CPU data.
# Only incompatible synthetic-lab metric indexes are removed.
for INDEX in $(os_curl -fsS "${OPENSEARCH_URL}/_cat/indices/metrics-*?h=index" 2>/dev/null || true); do
  MAPPING="$(os_curl -sS "${OPENSEARCH_URL}/${INDEX}/_mapping/field/cpu" || true)"
  if echo "${MAPPING}" | grep -q '"type":"keyword"'; then
    echo "Deleting incompatible metrics index ${INDEX} (cpu was mapped as keyword)..."
    os_curl -fsS -X DELETE "${OPENSEARCH_URL}/${INDEX}" >/dev/null
  fi
done

# Materialise both wildcard families immediately. Daily indexes are then
# created by Telegraf and Fluent Bit with the templates already installed.
for INDEX in metrics-bootstrap logs-bootstrap; do
  STATUS="$(os_curl -sS -o "/tmp/${INDEX}.json" -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${INDEX}" \
    -H "Content-Type: application/json" -d '{}')"
  case "${STATUS}" in
    200|201) echo "Created ${INDEX}." ;;
    400)
      if grep -q 'resource_already_exists_exception' "/tmp/${INDEX}.json"; then
        echo "${INDEX} already exists."
      else
        echo "Unable to create ${INDEX} (HTTP 400)." >&2
        cat "/tmp/${INDEX}.json" >&2
        exit 1
      fi
      ;;
    *)
      echo "Unable to create ${INDEX} (HTTP ${STATUS})." >&2
      cat "/tmp/${INDEX}.json" >&2
      exit 1
      ;;
  esac
done

echo "OpenSearch index templates and bootstrap indexes are ready."
