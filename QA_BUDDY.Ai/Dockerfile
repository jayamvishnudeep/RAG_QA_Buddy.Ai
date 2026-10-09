FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    QABUDDY_HOST=0.0.0.0 \
    QABUDDY_PORT=8000 \
    QABUDDY_DATA_DIR=/data \
    QABUDDY_STORAGE_DIR=/app/storage

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY qabuddy ./qabuddy
COPY web ./web
COPY config ./config
COPY eval ./eval

RUN useradd --create-home qabuddy && mkdir -p /app/storage && chown -R qabuddy /app/storage
USER qabuddy

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "-m", "qabuddy", "serve"]
