FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERB_CONFIG_FILE=/app/config/.env
WORKDIR /app
RUN groupadd --gid 10001 herb && useradd --uid 10001 --gid herb --no-create-home herb \
    && mkdir -p /app/config /app/store && chown -R herb:herb /app
COPY requirements.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=herb:herb app ./app
COPY --chown=herb:herb web ./web
COPY --chown=herb:herb data ./data
USER herb
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=3)"
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "7860"]

FROM base AS ocr
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-ocr.txt
USER herb

FROM base AS runtime
