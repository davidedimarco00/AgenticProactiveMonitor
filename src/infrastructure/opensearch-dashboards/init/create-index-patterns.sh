#!/bin/sh
set -eu

OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch:9200}"
DASHBOARDS_URL="${DASHBOARDS_URL:-http://opensearch-dashboards:5601}"
WAIT_SECONDS="${DASHBOARDS_WAIT_SECONDS:-180}"

wait_for_service() {
  name="$1"
  url="$2"
  elapsed=0

  while [ "${elapsed}" -lt "${WAIT_SECONDS}" ]; do
    status="$(curl -sS -o /tmp/service-status.json -w "%{http_code}" "${url}" || true)"
    if [ "${status}" = "200" ]; then
      echo "${name} is available."
      return 0
    fi

    echo "Waiting for ${name}..."
    sleep 3
    elapsed=$((elapsed + 3))
  done

  echo "${name} did not become available within ${WAIT_SECONDS}s." >&2
  cat /tmp/service-status.json >&2 || true
  return 1
}

ensure_bootstrap_index() {
  index="$1"
  response_file="/tmp/${index}.json"

  status="$(curl -sS -o "${response_file}" -w "%{http_code}" \
    -X PUT "${OPENSEARCH_URL}/${index}" \
    -H "Content-Type: application/json" \
    -d '{
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
      },
      "mappings": {
        "properties": {
          "@timestamp": {"type": "date"}
        }
      }
    }' || true)"

  case "${status}" in
    200|201)
      echo "Created physical index ${index}."
      ;;
    400)
      if grep -q 'resource_already_exists_exception' "${response_file}"; then
        echo "Physical index ${index} already exists."
      else
        echo "Unable to create physical index ${index} (HTTP 400)." >&2
        cat "${response_file}" >&2
        return 1
      fi
      ;;
    *)
      echo "Unable to create physical index ${index} (HTTP ${status})." >&2
      cat "${response_file}" >&2 || true
      return 1
      ;;
  esac
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
}

wait_for_service "OpenSearch" "${OPENSEARCH_URL}/_cluster/health"

# These concrete indexes make both wildcard families visible immediately, even
# before Telegraf and Fluent Bit write their first daily documents.
ensure_bootstrap_index metrics-bootstrap
ensure_bootstrap_index logs-bootstrap

wait_for_service "OpenSearch Dashboards" "${DASHBOARDS_URL}/api/status"

# Stable IDs make this operation idempotent. The objects are overwritten rather
# than duplicated every time the Compose stack starts.
create_or_update_index_pattern metrics-index-pattern "metrics-*"
create_or_update_index_pattern logs-index-pattern "logs-*"

echo "OpenSearch physical indexes and Dashboards index patterns are ready."
