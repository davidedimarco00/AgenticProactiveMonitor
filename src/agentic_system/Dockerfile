FROM python:3.12-slim
WORKDIR /app
COPY src/agentic_system/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app
CMD ["python", "-m", "src.agentic_system.main", "--config", "src/agentic_system/config/agents.yaml"]
