#!/bin/sh
set -eu

DASHBOARDS_URL="${DASHBOARDS_URL:-http://opensearch-dashboards:5601}"
WAIT_SECONDS="${DASHBOARDS_WAIT_SECONDS:-180}"

wait_for_dashboards() {
  elapsed=0
  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    status="$(curl -sS -o /tmp/dashboards-status.json -w "%{http_code}" \
      "${DASHBOARDS_URL}/api/status" || true)"
    if [ "${status}" = "200" ]; then
      echo "OpenSearch Dashboards is available."
      return 0
    fi

    echo "Waiting for OpenSearch Dashboards..."
    sleep 3
    elapsed=$((elapsed + 3))
  done

  echo "OpenSearch Dashboards did not become available within ${WAIT_SECONDS}s." >&2
  cat /tmp/dashboards-status.json >&2 || true
  return 1
}

create_or_update_index_pattern() {
  object_id="$1"
  title="$2"
  response_file="/tmp/${object_id}.json"

  payload="$(cat <<JSON
{
  "attributes": {
    "title": "${title}",
    "timeFieldName": "@timestamp"
  }
}
JSON
)"

  status="$(curl -sS -o "${response_file}" -w "%{http_code}" \
    -X POST \
    "${DASHBOARDS_URL}/api/saved_objects/index-pattern/${object_id}?overwrite=true" \
    -H "Content-Type: application/json" \
    -H "osd-xsrf: true" \
    -H "kbn-xsrf: true" \
    -d "${payload}" || true)"

  case "${status}" in
    200|201)
      echo "Created or updated Dashboards index pattern ${title}."
      ;;
    *)
      echo "Unable to create Dashboards index pattern ${title} (HTTP ${status})." >&2
      cat "${response_file}" >&2 || true
      return 1
      ;;
  esac

  verify_status="$(curl -sS -o "${response_file}" -w "%{http_code}" \
    "${DASHBOARDS_URL}/api/saved_objects/index-pattern/${object_id}" || true)"
  if [ "${verify_status}" != "200" ]; then
    echo "Dashboards did not persist index pattern ${title} (HTTP ${verify_status})." >&2
    cat "${response_file}" >&2 || true
    return 1
  fi
}

wait_for_dashboards
create_or_update_index_pattern metrics-index-pattern "metrics-*"
create_or_update_index_pattern logs-index-pattern "logs-*"

echo "OpenSearch Dashboards index patterns are ready."
