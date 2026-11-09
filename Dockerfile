# Multi-architecture image (arm64 / armv7 / amd64): works on a Raspberry Pi 4.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --system --uid 1000 portal && mkdir -p /data && chown portal /data
USER portal
VOLUME ["/data"]
EXPOSE 8080

# Local HTTP check only (no traffic to the inverter). A short interval makes the status
# switch from "starting" to "healthy" a few seconds after startup.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
