FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 agentic

COPY --chown=agentic:agentic agentic_system ./agentic_system
COPY --chown=agentic:agentic config ./config

USER agentic

CMD ["python", "-m", "agentic_system.main"]
