#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
DASHBOARDS_URL="${DASHBOARDS_URL:-http://opensearch-dashboards:5601}"
WAIT_SECONDS="${DASHBOARDS_WAIT_SECONDS:-300}"
HOSTS="${DASHBOARDS_INDEX_PATTERN_HOSTS:-traffic-generator api-gateway processing-service data-service worker-service}"

wait_for_dashboards() {
  elapsed=0
  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    if curl -fsS "${DASHBOARDS_URL}/api/status" >/dev/null 2>&1; then
      echo "OpenSearch Dashboards is available."
      return 0
    fi

    echo "Waiting for OpenSearch Dashboards..."
    sleep 3
    elapsed=$((elapsed + 3))
  done

  echo "OpenSearch Dashboards did not become available within ${WAIT_SECONDS}s." >&2
  return 1
}

wait_for_index() {
  index_pattern="$1"
  elapsed=0

  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    if curl -fsS "${OPENSEARCH_URL}/${index_pattern}/_count" >/dev/null 2>&1; then
      echo "Found indexes matching ${index_pattern}."
      return 0
    fi

    echo "Waiting for indexes matching ${index_pattern}..."
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "No index matching ${index_pattern} appeared within ${WAIT_SECONDS}s." >&2
  return 1
}

create_index_pattern() {
  object_id="$1"
  title="$2"

  response_file="/tmp/${object_id}.json"
  status="$(curl -sS -o "$response_file" -w "%{http_code}" \
    -X POST "${DASHBOARDS_URL}/api/saved_objects/index-pattern/${object_id}?overwrite=true" \
    -H "osd-xsrf: true" \
    -H "Content-Type: application/json" \
    -d "{\"attributes\":{\"title\":\"${title}\",\"timeFieldName\":\"@timestamp\"}}" || true)"

  case "$status" in
    200|201)
      echo "Created or updated Dashboards index pattern ${title}."
      ;;
    *)
      echo "Unable to create index pattern ${title} (HTTP ${status})." >&2
      cat "$response_file" >&2 || true
      return 1
      ;;
  esac
}

wait_for_dashboards

for host in $HOSTS; do
  metrics_pattern="metrics-${host}-*"
  logs_pattern="logs-${host}-*"

  wait_for_index "$metrics_pattern"
  wait_for_index "$logs_pattern"

  create_index_pattern "metrics-${host}" "$metrics_pattern"
  create_index_pattern "logs-${host}" "$logs_pattern"
done

echo "All monitored-system OpenSearch Dashboards index patterns are ready."
