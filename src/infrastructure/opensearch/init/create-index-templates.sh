#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"

wait_for_opensearch() {
  echo "Waiting for OpenSearch..."
  until curl -fsS "${OPENSEARCH_URL}/_cluster/health" >/dev/null; do
    sleep 3
  done
}

put_template() {
  name="$1"
  payload="$2"
  curl -fsS -X PUT "${OPENSEARCH_URL}/_index_template/${name}" \
    -H "Content-Type: application/json" \
    --data-binary "@${payload}" >/dev/null
  echo "Created index template: ${name}"
}

wait_for_opensearch

cat >/tmp/metrics-template.json <<'JSON'
{
  "index_patterns": ["metrics-machine-*"],
  "priority": 200,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "dynamic": true,
      "dynamic_templates": [
        {
          "telegraf_tags": {
            "path_match": "tag.*",
            "match_mapping_type": "string",
            "mapping": {"type": "keyword", "ignore_above": 256}
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
            "project": {"type": "keyword"},
            "environment": {"type": "keyword"},
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
            "usage_iowait": {"type": "float"}
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
            "used_percent": {"type": "float"},
            "available_percent": {"type": "float"}
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
  "_meta": {
    "description": "Telegraf metrics, separated by monitored machine"
  }
}
JSON

cat >/tmp/logs-template.json <<'JSON'
{
  "index_patterns": ["logs-machine-*"],
  "priority": 100,
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
        "project": {"type": "keyword"},
        "environment": {"type": "keyword"},
        "monitored_by": {"type": "keyword"},
        "log_source": {"type": "keyword"},
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
  "_meta": {
    "description": "Fluent Bit logs, separated by monitored machine"
  }
}
JSON

put_template metrics-machine-template /tmp/metrics-template.json
put_template logs-machine-template /tmp/logs-template.json

echo "OpenSearch templates are ready."
