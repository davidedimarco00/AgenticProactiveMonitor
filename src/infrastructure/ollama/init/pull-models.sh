#!/bin/sh
set -eu

wait_seconds="${OLLAMA_WAIT_SECONDS:-300}"
elapsed=0

echo "Waiting for Ollama at ${OLLAMA_HOST:-http://ollama:11434}..."

until ollama list >/dev/null 2>&1; do
  if [ "$elapsed" -ge "$wait_seconds" ]; then
    echo "Ollama did not become ready within ${wait_seconds}s." >&2
    exit 1
  fi

  sleep 2
  elapsed=$((elapsed + 2))
done

for model in "${OLLAMA_CHAT_MODEL:-}" "${OLLAMA_EMBEDDING_MODEL:-}"; do
  if [ -n "$model" ]; then
    echo "Ensuring Ollama model is available: $model"
    ollama pull "$model"
  fi
done

echo "Ollama models are ready."
