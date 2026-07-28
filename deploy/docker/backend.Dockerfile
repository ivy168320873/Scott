# Scott Studio 後端映像（同時服務 Studio API 與既有股市分析 app）。
#
# 同一個映像也用於 Celery worker，只是啟動指令不同 —— 確保 web 與 worker
# 的相依版本永遠一致。

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 編譯 asyncpg / greenlet 等套件需要 build tools；curl 供 healthcheck 使用。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# 先複製相依清單，讓相依層可被 Docker 快取。
COPY requirements.txt requirements-studio.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements-studio.txt \
    && pip install -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/studio/health/live || exit 1

CMD ["uvicorn", "studio_server:application", "--host", "0.0.0.0", "--port", "8000"]
