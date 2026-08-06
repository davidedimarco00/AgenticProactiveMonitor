#!/bin/bash

set -euo pipefail

HOST_ID=${HOST_ID:-unknown-machine}
MACHINE_ROLE=${MACHINE_ROLE:-generic-server}
LOG_LEVEL_PROFILE=${LOG_LEVEL_PROFILE:-normal}
LATENCY_MIN=${LATENCY_MIN:-10}
LATENCY_MAX=${LATENCY_MAX:-500}
LOG_INTERVAL=${LOG_INTERVAL:-3}

LOG_DIR="/var/log/machine"
APP_LOG_FILE="${LOG_DIR}/app.log"
SYSTEM_LOG_FILE="${LOG_DIR}/system.log"

mkdir -p "${LOG_DIR}"
touch "${APP_LOG_FILE}" "${SYSTEM_LOG_FILE}"

TELEGRAF_PID=""
FLUENTBIT_PID=""

cleanup() {
  local exit_code=$?

  if [ -n "${TELEGRAF_PID}" ] && kill -0 "${TELEGRAF_PID}" 2>/dev/null; then
    kill "${TELEGRAF_PID}" 2>/dev/null || true
  fi

  if [ -n "${FLUENTBIT_PID}" ] && kill -0 "${FLUENTBIT_PID}" 2>/dev/null; then
    kill "${FLUENTBIT_PID}" 2>/dev/null || true
  fi

  wait 2>/dev/null || true
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

echo "=================================================="
echo "Starting monitored machine"
echo "HOST_ID=${HOST_ID}"
echo "MACHINE_ROLE=${MACHINE_ROLE}"
echo "LOG_LEVEL_PROFILE=${LOG_LEVEL_PROFILE}"
echo "=================================================="

#############################
# START TELEGRAF
#############################
echo "Starting Telegraf..."

telegraf --config /etc/telegraf/telegraf.conf &
TELEGRAF_PID=$!

#############################
# START FLUENT BIT
#############################
echo "Starting Fluent Bit..."

# parsers.conf is already loaded through Parsers_File in fluent-bit.conf.
# Passing -R here as well registers app_json twice and can terminate Fluent Bit.
if [ -x /opt/fluent-bit/bin/fluent-bit ]; then
  /opt/fluent-bit/bin/fluent-bit \
    -c /etc/fluent-bit/fluent-bit.conf &
else
  fluent-bit \
    -c /etc/fluent-bit/fluent-bit.conf &
fi

FLUENTBIT_PID=$!

# Fail fast with an explicit process name instead of silently entering a
# restart loop while the telemetry initializer waits for documents.
sleep 3

if ! kill -0 "${TELEGRAF_PID}" 2>/dev/null; then
  echo "ERROR: Telegraf stopped during startup" >&2
  wait "${TELEGRAF_PID}" || true
  exit 1
fi

if ! kill -0 "${FLUENTBIT_PID}" 2>/dev/null; then
  echo "ERROR: Fluent Bit stopped during startup" >&2
  wait "${FLUENTBIT_PID}" || true
  exit 1
fi

#############################
# SYNTHETIC WORKLOAD
#############################
generate_level() {
  case "${LOG_LEVEL_PROFILE}" in
    normal)
      shuf -e INFO INFO INFO INFO WARN DEBUG -n 1
      ;;
    unstable)
      shuf -e INFO INFO WARN WARN ERROR DEBUG -n 1
      ;;
    critical)
      shuf -e INFO WARN ERROR ERROR ERROR DEBUG -n 1
      ;;
    *)
      shuf -e INFO WARN ERROR DEBUG -n 1
      ;;
  esac
}

generate_event_type() {
  shuf -e \
    request_processed \
    background_job_completed \
    cache_refreshed \
    database_query_executed \
    external_service_call \
    health_check_completed \
    configuration_loaded \
    user_session_updated \
    -n 1
}

generate_error_message() {
  shuf -e \
    "Temporary connection timeout" \
    "Slow response detected" \
    "Unexpected application latency" \
    "Retrying failed operation" \
    "Resource usage above expected baseline" \
    "Queue processing delay detected" \
    -n 1
}

generate_normal_message() {
  shuf -e \
    "Application event processed successfully" \
    "Service heartbeat completed" \
    "Request completed" \
    "Background task executed" \
    "Internal status check completed" \
    "Telemetry event generated" \
    -n 1
}

echo "Starting synthetic application workload..."

while true; do
  LEVEL=$(generate_level)
  EVENT_TYPE=$(generate_event_type)
  LATENCY=$(shuf -i "${LATENCY_MIN}-${LATENCY_MAX}" -n 1)
  CPU_SIGNAL=$(shuf -i 5-95 -n 1)
  MEMORY_SIGNAL=$(shuf -i 20-90 -n 1)
  REQUESTS=$(shuf -i 1-200 -n 1)
  ERROR_CODE=0

  if [ "${LEVEL}" = "ERROR" ]; then
    MESSAGE=$(generate_error_message)
    ERROR_CODE=$(shuf -i 500-599 -n 1)
  else
    MESSAGE=$(generate_normal_message)
  fi

  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  UPTIME_SECONDS=$(cut -d. -f1 /proc/uptime)
  LOAD_AVERAGE=$(awk '{print $1}' /proc/loadavg)

  echo "{
    \"timestamp\":\"${TIMESTAMP}\",
    \"host\":\"${HOST_ID}\",
    \"machine_role\":\"${MACHINE_ROLE}\",
    \"service\":\"synthetic-application\",
    \"event_type\":\"${EVENT_TYPE}\",
    \"level\":\"${LEVEL}\",
    \"message\":\"${MESSAGE}\",
    \"latency_ms\":${LATENCY},
    \"cpu_signal\":${CPU_SIGNAL},
    \"memory_signal\":${MEMORY_SIGNAL},
    \"requests\":${REQUESTS},
    \"error_code\":${ERROR_CODE}
  }" | jq -c . >> "${APP_LOG_FILE}"

  echo "{
    \"timestamp\":\"${TIMESTAMP}\",
    \"host\":\"${HOST_ID}\",
    \"machine_role\":\"${MACHINE_ROLE}\",
    \"component\":\"system-simulator\",
    \"level\":\"INFO\",
    \"message\":\"Synthetic system heartbeat\",
    \"uptime_seconds\":${UPTIME_SECONDS},
    \"load_average\":${LOAD_AVERAGE}
  }" | jq -c . >> "${SYSTEM_LOG_FILE}"

  sleep "${LOG_INTERVAL}"

  if ! kill -0 "${TELEGRAF_PID}" 2>/dev/null; then
    echo "ERROR: Telegraf process stopped unexpectedly" >&2
    wait "${TELEGRAF_PID}" || true
    exit 1
  fi

  if ! kill -0 "${FLUENTBIT_PID}" 2>/dev/null; then
    echo "ERROR: Fluent Bit process stopped unexpectedly" >&2
    wait "${FLUENTBIT_PID}" || true
    exit 1
  fi
done
