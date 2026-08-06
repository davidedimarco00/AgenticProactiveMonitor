#!/bin/bash

set -e

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

telegraf \
  --config /etc/telegraf/telegraf.conf &

TELEGRAF_PID=$!

#############################
# START FLUENT BIT
#############################
echo "Starting Fluent Bit..."

if [ -x /opt/fluent-bit/bin/fluent-bit ]; then
  /opt/fluent-bit/bin/fluent-bit \
    -c /etc/fluent-bit/fluent-bit.conf \
    -R /etc/fluent-bit/parsers.conf &
else
  fluent-bit \
    -c /etc/fluent-bit/fluent-bit.conf \
    -R /etc/fluent-bit/parsers.conf &
fi

FLUENTBIT_PID=$!

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

  # UTC with an explicit numeric offset matches the Fluent Bit %z parser and
  # OpenSearch date mapping without relying on local container time zones.
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S+0000")
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
    echo "Telegraf process stopped unexpectedly"
    exit 1
  fi

  if ! kill -0 "${FLUENTBIT_PID}" 2>/dev/null; then
    echo "Fluent Bit process stopped unexpectedly"
    exit 1
  fi
done
