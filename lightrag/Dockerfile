FROM python:3.12.13-slim-bookworm

# Use the official uv image so the LightRAG server can be installed as an
# isolated tool, as recommended by LightRAG.
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_TOOL_BIN_DIR=/usr/local/bin

WORKDIR /app

# Pin the independently deployed API surface used by the platform proxy.
# The API extra does not include PostgreSQL storage dependencies. Install the
# two adapters used by PGKV/PGDocStatus/PGGraph/PGVector in the same isolated
# tool environment so startup never needs to download packages at runtime.
RUN uv tool install \
    --python /usr/local/bin/python \
    --with "asyncpg>=0.31.0,<1.0.0" \
    --with "pgvector>=0.4.2,<1.0.0" \
    "lightrag-hku[api]==1.5.6"

RUN mkdir -p /var/lib/lightrag/work /var/lib/lightrag/input /var/log/lightrag

CMD ["lightrag-server", "--host", "0.0.0.0", "--port", "9621", "--working-dir", "/var/lib/lightrag/work"]
