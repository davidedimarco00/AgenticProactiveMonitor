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
  "index_patterns": [
    "metrics-traffic-generator-*",
    "metrics-api-gateway-*",
    "metrics-processing-service-*",
    "metrics-data-service-*",
    "metrics-worker-service-*"
  ],
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
            "monitored_by": {"type": "keyword"},
            "container_name": {"type": "keyword"},
            "container_image": {"type": "keyword"},
            "container_status": {"type": "keyword"},
            "network_target": {"type": "keyword"},
            "url": {"type": "keyword"},
            "server": {"type": "keyword"},
            "port": {"type": "keyword"},
            "protocol": {"type": "keyword"},
            "result": {"type": "keyword"}
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
        "docker_container_cpu": {
          "type": "object",
          "dynamic": true,
          "properties": {
            "usage_percent": {"type": "float"},
            "usage_total": {"type": "long"},
            "usage_in_usermode": {"type": "long"},
            "usage_in_kernelmode": {"type": "long"}
          }
        },
        "docker_container_mem": {
          "type": "object",
          "dynamic": true,
          "properties": {
            "usage": {"type": "long"},
            "usage_percent": {"type": "float"},
            "limit": {"type": "long"},
            "rss": {"type": "long"},
            "cache": {"type": "long"}
          }
        },
        "ping": {
          "type": "object",
          "dynamic": true,
          "properties": {
            "packets_transmitted": {"type": "integer"},
            "packets_received": {"type": "integer"},
            "percent_packet_loss": {"type": "float"},
            "average_response_ms": {"type": "float"},
            "minimum_response_ms": {"type": "float"},
            "maximum_response_ms": {"type": "float"},
            "percentile50_ms": {"type": "float"},
            "percentile95_ms": {"type": "float"},
            "percentile99_ms": {"type": "float"},
            "result_code": {"type": "integer"}
          }
        },
        "net_response": {
          "type": "object",
          "dynamic": true,
          "properties": {
            "response_time": {"type": "float"},
            "result_code": {"type": "integer"}
          }
        },
        "network_service_latency": {
          "type": "object",
          "dynamic": true,
          "properties": {
            "response_time": {"type": "float"},
            "result_code": {"type": "integer"}
          }
        },
        "docker_container_net": {"type": "object", "dynamic": true},
        "docker_container_blkio": {"type": "object", "dynamic": true},
        "docker_container_status": {"type": "object", "dynamic": true},
        "docker_container_health": {"type": "object", "dynamic": true},
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
    "description": "Telegraf metrics from the standalone monitored system"
  }
}
JSON

cat >/tmp/logs-template.json <<'JSON'
{
  "index_patterns": [
    "logs-traffic-generator-*",
    "logs-api-gateway-*",
    "logs-processing-service-*",
    "logs-data-service-*",
    "logs-worker-service-*"
  ],
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
    "description": "Fluent Bit logs from the standalone monitored system"
  }
}
JSON

put_template monitored-system-metrics-template /tmp/metrics-template.json
put_template monitored-system-logs-template /tmp/logs-template.json

echo "OpenSearch templates are ready."
