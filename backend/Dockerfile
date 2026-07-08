# ==================== 阶段1：依赖安装 ====================
FROM python:3.12-slim AS builder

# 安装 uv — 比 pip 快 10-100 倍的包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先复制依赖声明文件，利用 Docker 缓存层：依赖不变时跳过安装
COPY pyproject.toml uv.lock ./

# 使用 uv 安装依赖到虚拟环境（--frozen 保证 lock 文件一致性）
RUN uv sync --frozen --no-dev --no-install-project

# ==================== 阶段2：运行时镜像 ====================
FROM python:3.12-slim AS runtime

# 安装运行时系统依赖（PostgreSQL 客户端库等）
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从 builder 阶段复制虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 将虚拟环境加入 PATH，使 python/uvicorn 直接可用
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 复制应用源码
COPY backend/ ./backend/

# 创建非 root 用户运行应用，遵循最小权限原则
RUN groupadd -r copilot && useradd -r -g copilot -d /app copilot && \
    mkdir -p /app/uploads && chown -R copilot:copilot /app
USER copilot

EXPOSE 8000

# 健康检查：容器启动后定期验证服务是否正常
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动 FastAPI 应用
# 先用 alembic 执行数据库迁移（-c 指定 alembic.ini 位置），再启动 uvicorn
# 这样容器首次启动时会自动创建表结构，避免手动跑迁移
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
