# syntax=docker/dockerfile:1.7
FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=5

WORKDIR /app

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip <<'EOF'
python - <<'PY' > /tmp/runtime-requirements.txt
import tomllib

with open("pyproject.toml", "rb") as source:
    configuration = tomllib.load(source)

requirements = [
    *configuration["build-system"]["requires"],
    *configuration["project"]["dependencies"],
]
print("\n".join(requirements))
PY
python -m pip install --default-timeout=180 --retries=5 -r /tmp/runtime-requirements.txt
rm -f /tmp/runtime-requirements.txt
EOF

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY worker.py ./worker.py
RUN python -m pip install --no-deps --no-build-isolation .

EXPOSE 8000
