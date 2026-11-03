# Demo API image. Build: docker build -t mlratelimit-demo .
FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies first, so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra demo --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra demo --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY demo ./demo
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Hosting platforms such as Render pick the port and pass it in $PORT; locally it is 8000.
# The timeout is generous because starting Python takes about 3 s on a 0.1 CPU free instance.
HEALTHCHECK --interval=5s --timeout=10s --retries=5 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000'), timeout=2)"]
CMD ["sh", "-c", "exec uvicorn demo.app:app --host 0.0.0.0 --port \"${PORT:-8000}\" --no-access-log"]
