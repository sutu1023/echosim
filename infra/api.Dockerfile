ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/api
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM dependencies AS test
RUN uv sync --locked --no-install-project
COPY api/ ./
COPY scripts/ /app/scripts/
COPY docs/ /app/docs/
COPY config/ /app/config/
COPY infra/ /app/infra/
COPY compose.yaml compose.test.yaml /app/
COPY openspec/ /app/openspec/
COPY .agents/ /app/.agents/
COPY README.md AGENTS.md /app/
ENV PATH="/app/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["python", "-m", "pytest", "-v"]

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app/api
RUN adduser -D -u 10001 app && mkdir -p /app/logs && chown 10001:10001 /app/logs
COPY --from=dependencies --chown=10001:10001 /app/api/.venv .venv
COPY --chown=10001:10001 api/app/ app/
COPY --chown=10001:10001 api/migrations/ migrations/
COPY --chown=10001:10001 api/alembic.ini ./
ENV PATH="/app/api/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["python", "-m", "app.server"]
