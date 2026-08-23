#!/bin/bash

set -euo pipefail

HOST_ID=${HOST_ID:-unknown-machine}
MACHINE_ROLE=${MACHINE_ROLE:-generic-server}
LOG_LEVEL_PROFILE=${LOG_LEVEL_PROFILE:-normal}
LATENCY_MIN=${LATENCY_MIN:-10}
LATENCY_MAX=${LATENCY_MAX:-500}
LOG_INTERVAL=${LOG_INTERVAL:-3}
WORKLOAD_MODE=${WORKLOAD_MODE:-synthetic}
APP_COMMAND=${APP_COMMAND:-}

LOG_DIR="/var/log/machine"
APP_LOG_FILE="${LOG_DIR}/app.log"
SYSTEM_LOG_FILE="${LOG_DIR}/system.log"
RUNTIME_LOG_FILE="${LOG_DIR}/runtime.log"

mkdir -p "${LOG_DIR}"
touch "${APP_LOG_FILE}" "${SYSTEM_LOG_FILE}" "${RUNTIME_LOG_FILE}"

TELEGRAF_PID=""
FLUENTBIT_PID=""
APP_PID=""

cleanup() {
  local exit_code=$?

  for pid in "${APP_PID}" "${TELEGRAF_PID}" "${FLUENTBIT_PID}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done

  wait 2>/dev/null || true
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

echo "=================================================="
echo "Starting monitored machine"
echo "HOST_ID=${HOST_ID}"
echo "MACHINE_ROLE=${MACHINE_ROLE}"
echo "WORKLOAD_MODE=${WORKLOAD_MODE}"
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

if [ -x /opt/fluent-bit/bin/fluent-bit ]; then
  /opt/fluent-bit/bin/fluent-bit -c /etc/fluent-bit/fluent-bit.conf &
else
  fluent-bit -c /etc/fluent-bit/fluent-bit.conf &
fi

FLUENTBIT_PID=$!

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
# APPLICATION / SYNTHETIC MODE
#############################
if [ "${WORKLOAD_MODE}" = "application" ]; then
  if [ -z "${APP_COMMAND}" ]; then
    echo "ERROR: WORKLOAD_MODE=application requires APP_COMMAND" >&2
    exit 1
  fi

  echo "Starting monitored application..."
  bash -lc "${APP_COMMAND}" \
    > >(tee -a "${RUNTIME_LOG_FILE}") \
    2> >(tee -a "${RUNTIME_LOG_FILE}" >&2) &
  APP_PID=$!

  sleep 2
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "ERROR: monitored application stopped during startup" >&2
    wait "${APP_PID}" || true
    exit 1
  fi
else
  echo "Starting synthetic application workload..."
fi

#############################
# SYNTHETIC WORKLOAD HELPERS
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

write_system_heartbeat() {
  local timestamp uptime_seconds load_average

  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  uptime_seconds=$(cut -d. -f1 /proc/uptime)
  load_average=$(awk '{print $1}' /proc/loadavg)

  echo "{
    \"timestamp\":\"${timestamp}\",
    \"host\":\"${HOST_ID}\",
    \"machine_role\":\"${MACHINE_ROLE}\",
    \"component\":\"system-simulator\",
    \"level\":\"INFO\",
    \"message\":\"System heartbeat\",
    \"uptime_seconds\":${uptime_seconds},
    \"load_average\":${load_average}
  }" | jq -c . >> "${SYSTEM_LOG_FILE}"
}

write_synthetic_application_event() {
  local level event_type latency cpu_signal memory_signal requests error_code
  local message timestamp

  level=$(generate_level)
  event_type=$(generate_event_type)
  latency=$(shuf -i "${LATENCY_MIN}-${LATENCY_MAX}" -n 1)
  cpu_signal=$(shuf -i 5-95 -n 1)
  memory_signal=$(shuf -i 20-90 -n 1)
  requests=$(shuf -i 1-200 -n 1)
  error_code=0

  if [ "${level}" = "ERROR" ]; then
    message=$(generate_error_message)
    error_code=$(shuf -i 500-599 -n 1)
  else
    message=$(generate_normal_message)
  fi

  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  echo "{
    \"timestamp\":\"${timestamp}\",
    \"host\":\"${HOST_ID}\",
    \"machine_role\":\"${MACHINE_ROLE}\",
    \"service\":\"synthetic-application\",
    \"event_type\":\"${event_type}\",
    \"level\":\"${level}\",
    \"message\":\"${message}\",
    \"latency_ms\":${latency},
    \"cpu_signal\":${cpu_signal},
    \"memory_signal\":${memory_signal},
    \"requests\":${requests},
    \"error_code\":${error_code}
  }" | jq -c . >> "${APP_LOG_FILE}"
}

#############################
# MAIN LOOP
#############################
while true; do
  write_system_heartbeat

  if [ "${WORKLOAD_MODE}" = "synthetic" ]; then
    write_synthetic_application_event
  fi

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

  if [ "${WORKLOAD_MODE}" = "application" ] && ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "ERROR: monitored application stopped unexpectedly" >&2
    wait "${APP_PID}" || true
    exit 1
  fi
done
