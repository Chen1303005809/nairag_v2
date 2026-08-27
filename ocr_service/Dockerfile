FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_CACHE_HOME=/var/lib/nairag-ocr/model-cache

WORKDIR /app

COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends --yes libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 ocr \
    && mkdir -p /var/lib/nairag-ocr/model-cache \
    && chown -R ocr:ocr /app /var/lib/nairag-ocr

COPY app ./app
RUN chown -R ocr:ocr /app

USER ocr

EXPOSE 9003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9003"]
