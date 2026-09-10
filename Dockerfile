FROM python:3.12-slim

# tzdata so zoneinfo can resolve TZ (America/Chicago) inside the slim image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/lake.db

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Non-root; /data is the mounted volume, so it must be writable by this uid.
RUN useradd --create-home --uid 1000 lake \
 && mkdir -p /data \
 && chown -R lake:lake /srv /data
USER lake

EXPOSE 9011

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9011/health', timeout=8).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9011"]
