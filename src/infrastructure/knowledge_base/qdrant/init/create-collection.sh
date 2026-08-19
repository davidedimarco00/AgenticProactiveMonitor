#!/bin/sh
set -eu

qdrant_url="${QDRANT_URL:-http://qdrant:6333}"
raw_collections="${QDRANT_COLLECTIONS:-${QDRANT_COLLECTION:-monitored-system}}"
vector_size="${QDRANT_VECTOR_SIZE:-384}"
distance="${QDRANT_DISTANCE:-Cosine}"
wait_seconds="${QDRANT_WAIT_SECONDS:-120}"
elapsed=0

# QDRANT_COLLECTIONS is a comma-separated list so it can be safely stored in .env.
collections="$(printf '%s' "$raw_collections" | tr ',' ' ')"

echo "Waiting for Qdrant at ${qdrant_url}..."

until curl -fsS "${qdrant_url}/readyz" >/dev/null 2>&1; do
  if [ "$elapsed" -ge "$wait_seconds" ]; then
    echo "Qdrant did not become ready within ${wait_seconds}s." >&2
    exit 1
  fi

  sleep 2
  elapsed=$((elapsed + 2))
done

ensure_collection() {
  collection="$1"

  status="$(
    curl -sS \
      -o /tmp/qdrant-collection.json \
      -w '%{http_code}' \
      "${qdrant_url}/collections/${collection}"
  )"

  case "$status" in
    200)
      echo "Qdrant collection already exists: ${collection}"
      ;;
    404)
      echo "Creating Qdrant collection: ${collection}"
      curl -fsS \
        -X PUT \
        -H "Content-Type: application/json" \
        -d "{\"vectors\":{\"size\":${vector_size},\"distance\":\"${distance}\"}}" \
        "${qdrant_url}/collections/${collection}" >/dev/null
      ;;
    *)
      echo "Unexpected Qdrant response (${status}) while checking ${collection}." >&2
      cat /tmp/qdrant-collection.json >&2 || true
      exit 1
      ;;
  esac

  curl -fsS "${qdrant_url}/collections/${collection}" >/dev/null
  echo "Qdrant collection is ready: ${collection} (${vector_size}, ${distance})"
}

for collection in $collections; do
  if [ -n "$collection" ]; then
    ensure_collection "$collection"
  fi
done
