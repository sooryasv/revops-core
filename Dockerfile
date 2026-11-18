# syntax=docker/dockerfile:1
# One deployable (03 section 1, 13): stage 1 builds the SPA, stage 2 is the Python runtime serving API + SPA.
# WROTE-NOT-RUN in the build sandbox (no Docker daemon); see docs/LOCAL_VERIFICATION.md.

# ---- stage 1: build the SPA
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: runtime
FROM python:3.15-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    STATIC_DIR=/app/app/static

WORKDIR /app
# Locked, production-only dependencies first so this layer caches until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
COPY contract/ /contract/
COPY infra/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --from=web /web/dist ./app/static

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chmod 0555 /usr/local/bin/entrypoint.sh \
    && chown -R app:app /app
USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
