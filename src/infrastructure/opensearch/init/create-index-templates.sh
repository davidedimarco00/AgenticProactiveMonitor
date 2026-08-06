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

echo "Creating metrics index template for native Telegraf OpenSearch documents..."
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
              "path": {"type": "keyword"},
              "device": {"type": "keyword"},
              "interface": {"type": "keyword"},
              "environment": {"type": "keyword"},
              "project": {"type": "keyword"},
              "monitored_by": {"type": "keyword"}
            }
          },
          "cpu": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "usage_active": {"type": "float"},
              "usage_idle": {"type": "float"},
              "usage_user": {"type": "float"},
              "usage_system": {"type": "float"},
              "usage_iowait": {"type": "float"},
              "usage_irq": {"type": "float"},
              "usage_softirq": {"type": "float"},
              "usage_steal": {"type": "float"},
              "usage_guest": {"type": "float"},
              "usage_guest_nice": {"type": "float"},
              "usage_nice": {"type": "float"}
            }
          },
          "mem": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "total": {"type": "long"},
              "available": {"type": "long"},
              "used": {"type": "long"},
              "free": {"type": "long"},
              "cached": {"type": "long"},
              "buffered": {"type": "long"},
              "active": {"type": "long"},
              "inactive": {"type": "long"},
              "used_percent": {"type": "float"},
              "available_percent": {"type": "float"}
            }
          },
          "disk": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "total": {"type": "long"},
              "free": {"type": "long"},
              "used": {"type": "long"},
              "used_percent": {"type": "float"},
              "inodes_total": {"type": "long"},
              "inodes_free": {"type": "long"},
              "inodes_used": {"type": "long"},
              "inodes_used_percent": {"type": "float"}
            }
          },
          "diskio": {"type": "object", "dynamic": true},
          "net": {"type": "object", "dynamic": true},
          "system": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "load1": {"type": "float"},
              "load5": {"type": "float"},
              "load15": {"type": "float"},
              "uptime": {"type": "long"},
              "n_users": {"type": "long"}
            }
          },
          "swap": {
            "type": "object",
            "dynamic": true,
            "properties": {
              "total": {"type": "long"},
              "free": {"type": "long"},
              "used": {"type": "long"},
              "used_percent": {"type": "float"}
            }
          },
          "processes": {"type": "object", "dynamic": true},
          "kernel": {"type": "object", "dynamic": true}
        }
      }
    },
    "priority": 200,
    "version": 4,
    "_meta": {
      "description": "Native Telegraf document contract for all configured infrastructure inputs"
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
    "version": 3,
    "_meta": {
      "description": "Fluent Bit application and system log contract"
    }
  }' >/dev/null

echo "Logs index template created."

# Remove only indexes from older revisions that are structurally incompatible.
# Real daily indexes with the native object structure are preserved.
for INDEX in $(os_curl -fsS "${OPENSEARCH_URL}/_cat/indices/metrics-*?h=index" 2>/dev/null || true); do
  MAPPING="$(os_curl -sS "${OPENSEARCH_URL}/${INDEX}/_mapping/field/cpu" || true)"
  if echo "${MAPPING}" | grep -q '"type":"keyword"'; then
    echo "Deleting incompatible metrics index ${INDEX} (cpu was mapped as keyword)..."
    os_curl -fsS -X DELETE "${OPENSEARCH_URL}/${INDEX}" >/dev/null
  fi
done

# Empty bootstrap indexes were useful before collectors started, but they make
# Discover and field discovery less clear. The new startup sequence waits for
# real Telegraf and Fluent Bit documents instead.
for INDEX in metrics-bootstrap logs-bootstrap; do
  STATUS="$(os_curl -sS -o "/tmp/${INDEX}-delete.json" -w "%{http_code}" \
    -X DELETE "${OPENSEARCH_URL}/${INDEX}" || true)"
  case "${STATUS}" in
    200) echo "Removed legacy empty index ${INDEX}." ;;
    404) echo "${INDEX} is not present." ;;
    *)
      echo "Unable to remove ${INDEX} (HTTP ${STATUS})." >&2
      cat "/tmp/${INDEX}-delete.json" >&2 || true
      exit 1
      ;;
  esac
done

echo "OpenSearch index templates are ready."
