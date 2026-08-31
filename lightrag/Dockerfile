FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pin the independently deployed API surface used by the platform proxy.
RUN pip install --no-cache-dir "lightrag-hku==1.5.6"

RUN mkdir -p /var/lib/lightrag/work /var/lib/lightrag/input /var/log/lightrag

CMD ["lightrag-server", "--host", "0.0.0.0", "--port", "9621", "--working-dir", "/var/lib/lightrag/work"]
