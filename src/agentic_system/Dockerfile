FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
COPY agentic_system ./agentic_system
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 agentic

COPY --chown=agentic:agentic config ./config

EXPOSE 8081 8101 8102 8103 8104 8105

USER agentic

CMD ["python", "-m", "agentic_system.main"]
