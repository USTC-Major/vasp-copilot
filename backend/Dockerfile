# VASP-Doctor 后端镜像（13.2：多阶段构建、非 root、healthcheck；不安装 VASP/POTCAR）
# 布局与仓库根一致：源码位于 /repo/backend/app，使两棵导入树与本地开发完全等价：
#   - PYTHONPATH=/repo              让 BE-A 的 backend.app.* 绝对导入可解析
#   - uvicorn --app-dir /repo/backend  让医生端 app.main:app 相对导入可解析
FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/repo
WORKDIR /repo
COPY . ./backend
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin vaspdoctor \
    && mkdir -p /data/app /data/tmp \
    && chown -R vaspdoctor:vaspdoctor /repo /data
USER vaspdoctor
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=8s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--app-dir", "/repo/backend", "--host", "0.0.0.0", "--port", "8000"]