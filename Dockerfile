FROM node:24-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS frontend-build

WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm install --global npm@10.9.2 \
    && npm ci
COPY frontend ./frontend
RUN npm run build


FROM python:3.11-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS python-build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.3
COPY pyproject.toml uv.lock README.md ./
COPY backend/src ./backend/src
RUN uv sync --frozen --no-dev --extra ocr --no-editable --no-build-package pypdfium2


FROM python:3.11-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS runtime

ARG MANGASENSEI_VERSION=dev
LABEL org.opencontainers.image.title="MangaSensei" \
      org.opencontainers.image.version="${MANGASENSEI_VERSION}" \
      org.opencontainers.image.licenses="GPL-3.0-only"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MANGASENSEI_FRONTEND_DIST=/app/frontend/dist \
    HOME=/tmp

WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 mangasensei \
    && useradd --uid 10001 --gid mangasensei --no-create-home --shell /usr/sbin/nologin mangasensei \
    && groupadd --gid 10002 mangasensei-pdf \
    && useradd --uid 10002 --gid mangasensei-pdf --no-create-home --shell /usr/sbin/nologin mangasensei-pdf \
    && mkdir -p /app/var/storage /app/var/models /app/var/data /app/var/pdf-spool /app/var/pdf-renderer-output \
    && chown -R mangasensei:mangasensei /app/var

COPY --from=python-build /app/.venv /app/.venv
RUN rm -f /app/.venv/lib/python3.11/site-packages/torch/bin/test_interpreter_async.pt
COPY --chown=mangasensei:mangasensei backend ./backend
COPY --chown=mangasensei:mangasensei pyproject.toml uv.lock README.md VERSION ./
COPY --from=frontend-build --chown=mangasensei:mangasensei /app/frontend/dist ./frontend/dist

USER mangasensei
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["mangasensei", "api", "--host", "0.0.0.0", "--port", "8000"]
