# Discord bot image (works on Fly.io / Railway / any Docker host / a VPS).
# The static site is on GitHub Pages; this image only runs the bot.
FROM python:3.12-slim

# uv for fast, locked installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .

# Install exactly what's in uv.lock (bot + LLM extras; no dev tooling).
RUN uv sync --frozen --no-dev --extra bot --extra llm

# SQLite lives on a mounted volume so subscriptions survive restarts/redeploys.
ENV DB_PATH=/data/tracker.db
ENV PYTHONUNBUFFERED=1  # flush print()/logs straight to `docker logs`
VOLUME ["/data"]

# Reads config from env: DISCORD_TOKEN (required), EVENTS_SOURCE, SITE_URL,
# GITHUB_REPO, GOOGLE_CLOUD_PROJECT, GOOGLE_APPLICATION_CREDENTIALS, LLM_MODEL.
CMD ["uv", "run", "--no-sync", "--extra", "bot", "--extra", "llm", "python", "-m", "bot.main"]
