FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
COPY agentic_system ./agentic_system
RUN pip install --no-cache-dir .
RUN python -c "import agentspeak, agentspeak.runtime, agentspeak.stdlib; from mcp.client.streamable_http import streamablehttp_client; assert callable(streamablehttp_client)"

RUN useradd --create-home --uid 10001 agentic

COPY --chown=agentic:agentic config ./config

EXPOSE 8081 8082 8101 8102 8103 8104 8105

USER agentic

CMD ["python", "-m", "agentic_system.main"]
