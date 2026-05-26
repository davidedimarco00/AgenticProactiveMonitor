#!/bin/sh

set -e

OPENSEARCH_URL="http://opensearch:9200"

echo "Waiting for OpenSearch..."

until curl -s "${OPENSEARCH_URL}/_cluster/health" > /dev/null; do
  echo "OpenSearch is not ready yet..."
  sleep 5
done

echo "OpenSearch is available."

#############################
# METRICS INDEX TEMPLATE
#############################
echo "Creating metrics index template..."

curl -s -X PUT "${OPENSEARCH_URL}/_index_template/metrics-template" \
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
          "@timestamp": {
            "type": "date"
          },
          "host": {
            "type": "keyword"
          },
          "host_id": {
            "type": "keyword"
          },
          "machine_role": {
            "type": "keyword"
          },
          "environment": {
            "type": "keyword"
          },
          "project": {
            "type": "keyword"
          },
          "monitored_by": {
            "type": "keyword"
          },
          "usage_system": {
            "type": "float"
          },
          "usage_user": {
            "type": "float"
          },
          "usage_idle": {
            "type": "float"
          },
          "usage_active": {
            "type": "float"
          },
          "used_percent": {
            "type": "float"
          },
          "available_percent": {
            "type": "float"
          },
          "load1": {
            "type": "float"
          },
          "load5": {
            "type": "float"
          },
          "load15": {
            "type": "float"
          }
        }
      }
    },
    "priority": 100
  }'

echo ""
echo "Metrics index template created."

#############################
# LOGS INDEX TEMPLATE
#############################
echo "Creating logs index template..."

curl -s -X PUT "${OPENSEARCH_URL}/_index_template/logs-template" \
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
          "@timestamp": {
            "type": "date"
          },
          "timestamp": {
            "type": "date"
          },
          "host": {
            "type": "keyword"
          },
          "host.name": {
            "type": "keyword"
          },
          "host.id": {
            "type": "keyword"
          },
          "machine.role": {
            "type": "keyword"
          },
          "machine_role": {
            "type": "keyword"
          },
          "environment": {
            "type": "keyword"
          },
          "project": {
            "type": "keyword"
          },
          "monitored_by": {
            "type": "keyword"
          },
          "service": {
            "type": "keyword"
          },
          "component": {
            "type": "keyword"
          },
          "event_type": {
            "type": "keyword"
          },
          "level": {
            "type": "keyword"
          },
          "message": {
            "type": "text"
          },
          "latency_ms": {
            "type": "float"
          },
          "cpu_signal": {
            "type": "float"
          },
          "memory_signal": {
            "type": "float"
          },
          "requests": {
            "type": "integer"
          },
          "error_code": {
            "type": "integer"
          },
          "uptime_seconds": {
            "type": "long"
          },
          "load_average": {
            "type": "float"
          }
        }
      }
    },
    "priority": 100
  }'

echo ""
echo "Logs index template created."

#############################
# OPTIONAL INITIAL INDEXES
#############################
echo "Creating initial empty indexes..."

for MACHINE in machine-01 machine-02 machine-03 machine-04 machine-05
do
  TODAY=$(date +%Y.%m.%d)

  curl -s -X PUT "${OPENSEARCH_URL}/metrics-${MACHINE}-${TODAY}" \
    -H "Content-Type: application/json" \
    -d '{}'

  curl -s -X PUT "${OPENSEARCH_URL}/logs-${MACHINE}-${TODAY}" \
    -H "Content-Type: application/json" \
    -d '{}'

  echo "Initialized indexes for ${MACHINE}"
done

echo ""
echo "OpenSearch initialization completed successfully."