FROM python:3.11.11-bookworm AS base

WORKDIR /app

# System deps for any wheels that need to compile (rare with our pinned set,
# but kept for safety with grpcio / pyarrow on slim variants).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps using fully pinned requirements.txt (top-level + transitive).
# Done before COPY of source so that source changes don't bust this layer cache.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY pre_earnings/ ./pre_earnings/
COPY ects/ ./ects/
# Prompt templates baked in as a fallback. In K8s the same dir is overlaid
# by the earnings-intel-prompts-config ConfigMap mounted on /app/prompts.
COPY prompts/ ./prompts/

# Entrypoint dispatches by APP_MODE (which lives inside /app/.env,
# assembled by the initContainer at Pod start - not in shell env).
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER 1000:1000
ENTRYPOINT ["/entrypoint.sh"]
