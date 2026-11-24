# syntax=docker/dockerfile:1

# ---- 阶段 1：构建前端静态产物 ----
FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 阶段 2：Python 运行时，同域托管 API 与前端 ----
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=web-build /build/web/dist ./web/dist

RUN useradd --create-home --uid 10001 echosim \
    && mkdir -p /app/data/documents \
    && chown -R echosim:echosim /app
USER echosim

# ---- 阶段 3：业务建图 worker，只额外安装 Zep 客户端 ----
FROM runtime AS worker-graph

USER root
COPY app/requirements-worker-graph.txt ./requirements-worker-graph.txt
RUN python -m pip install --no-cache-dir -r requirements-worker-graph.txt \
    && python -m pip check \
    && python -c "import importlib.metadata as m; import zep_cloud; from zep_cloud import Zep; assert m.version('zep-cloud') == '4.1.0'"
USER echosim

# ---- 阶段 4：HTTP app，不安装业务建图 worker 依赖 ----
FROM runtime AS app

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez', timeout=2)"

# 启动前先应用数据库迁移（幂等），失败则容器退出并由 compose 重启。
CMD ["sh", "-c", "python -m app.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
