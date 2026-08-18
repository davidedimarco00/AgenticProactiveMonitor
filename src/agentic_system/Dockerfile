FROM gradle:8.10.2-jdk17 AS jason-builder

WORKDIR /workspace/jason
COPY agentic_system/bdi/jason ./
RUN gradle --no-daemon installDist


FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=jason-builder /workspace/jason/build/install/apm-jason-bdi /opt/apm-jason-bdi

COPY pyproject.toml ./pyproject.toml
COPY agentic_system ./agentic_system
RUN pip install --no-cache-dir .
RUN python -c "from mcp.client.streamable_http import streamablehttp_client; assert callable(streamablehttp_client)"
RUN /opt/apm-jason-bdi/bin/apm-jason-bdi \
      /app/agentic_system/bdi/jason/agents/technical_lead.asl \
      begin-triage \
      INC-BUILD-SMOKE \
      none \
      none \
      system_engineer,network_engineer,application_engineer,software_developer \
      > /tmp/jason-begin-smoke.txt \
    && grep -F "OK" /tmp/jason-begin-smoke.txt \
    && grep -F "manage_incident" /tmp/jason-begin-smoke.txt \
    && grep -F "triage_incident" /tmp/jason-begin-smoke.txt
RUN /opt/apm-jason-bdi/bin/apm-jason-bdi \
      /app/agentic_system/bdi/jason/agents/technical_lead.asl \
      select-primary \
      INC-BUILD-SMOKE \
      system \
      system_engineer \
      system_engineer,network_engineer,application_engineer,software_developer \
      > /tmp/jason-select-smoke.txt \
    && grep -F "OK" /tmp/jason-select-smoke.txt \
    && grep -F "manage_incident" /tmp/jason-select-smoke.txt \
    && grep -F "select_primary_investigator" /tmp/jason-select-smoke.txt \
    && grep -F "system_engineer" /tmp/jason-select-smoke.txt

RUN useradd --create-home --uid 10001 agentic

COPY --chown=agentic:agentic config ./config

EXPOSE 8081 8082 8101 8102 8103 8104 8105

USER agentic

CMD ["python", "-m", "agentic_system.main"]
