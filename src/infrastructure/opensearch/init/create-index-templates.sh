#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"

echo "Waiting for OpenSearch..."
until curl -fsS "${OPENSEARCH_URL}/_cluster/health" >/dev/null; do
  echo "OpenSearch is not ready yet..."
  sleep 3
done

echo "OpenSearch is available."

echo "Creating metrics index template..."
curl -fsS -X PUT "${OPENSEARCH_URL}/_index_template/metrics-template" \
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
        "properties": {
          "@timestamp": {"type": "date"},
          "name": {"type": "keyword"},
          "host": {"type": "keyword"},
          "host_id": {"type": "keyword"},
          "machine_role": {"type": "keyword"},
          "metric_type": {"type": "keyword"},
          "environment": {"type": "keyword"},
          "project": {"type": "keyword"},
          "monitored_by": {"type": "keyword"},
          "cpu": {"type": "keyword"},
          "usage_system": {"type": "float"},
          "usage_user": {"type": "float"},
          "usage_idle": {"type": "float"},
          "usage_active": {"type": "float"},
          "total": {"type": "long"},
          "available": {"type": "long"},
          "used": {"type": "long"},
          "used_percent": {"type": "float"},
          "available_percent": {"type": "float"},
          "load1": {"type": "float"},
          "load5": {"type": "float"},
          "load15": {"type": "float"}
        }
      }
    },
    "priority": 100
  }' >/dev/null

echo "Metrics index template created."

echo "Creating logs index template..."
curl -fsS -X PUT "${OPENSEARCH_URL}/_index_template/logs-template" \
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
    "priority": 100
  }' >/dev/null

echo "Logs index template created."

# Materialise both templates immediately. The real daily indexes are then
# created automatically by Telegraf and Fluent Bit.
for INDEX in metrics-bootstrap logs-bootstrap; do
  STATUS="$(curl -sS -o "/tmp/${INDEX}.json" -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${INDEX}" \
    -H "Content-Type: application/json" -d '{}')"
  case "${STATUS}" in
    200|201) echo "Created ${INDEX}." ;;
    400) echo "${INDEX} already exists." ;;
    *)
      echo "Unable to create ${INDEX} (HTTP ${STATUS})." >&2
      cat "/tmp/${INDEX}.json" >&2
      exit 1
      ;;
  esac
done
