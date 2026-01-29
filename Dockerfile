FROM python:3.11-slim

WORKDIR /app

# Better runtime defaults for containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Required by the leaderboard runner healthchecks (curl -f .../.well-known/agent-card.json)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
# (Optional) metadata file
COPY agent-card.json .

# Expose A2A port (scenario runner expects 9009)
EXPOSE 9009

# The scenario runner injects: --host/--port/--card-url
ENTRYPOINT ["python", "-m", "src.server"]

