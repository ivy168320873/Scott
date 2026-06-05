FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; asyncpg/httpx/websockets are pure-wheel installs.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway / most PaaS inject $PORT at runtime; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

# Bind to the platform-provided port. Schema auto-applies on startup (app/db.py).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
