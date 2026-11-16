ARG PYTHON_IMAGE=python:3.10-slim-bookworm
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_AGENT_CONFIG=/app/configs/safe-analysis.runtime.json \
    DATA_AGENT_SALES_URL=sqlite:////app/data/safe-analysis-demo.sqlite3
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git libgomp1 libpq-dev pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY packages ./packages
COPY pyproject.toml README.md ./
COPY skills ./skills
COPY pilot/meta_data/alembic.ini ./pilot/meta_data/alembic.ini
COPY pilot/meta_data/alembic ./pilot/meta_data/alembic
RUN pip install --no-cache-dir \
    ./packages/dbgpt-core ./packages/dbgpt-client ./packages/dbgpt-ext \
    ./packages/dbgpt-sandbox ./packages/dbgpt-accelerator/dbgpt-acc-auto \
    './packages/dbgpt-serve[safe_analysis_mcp,safe_analysis_files,safe_analysis_databases,safe_analysis_qdrant]' \
    ./packages/dbgpt-app
COPY configs/safe-analysis.example.json ./configs/safe-analysis.example.json
COPY configs/data-agent.example.toml ./configs/data-agent.toml
COPY examples/safe_analysis ./examples/safe_analysis
COPY docker/data-agent/entrypoint.py ./docker/data-agent/entrypoint.py
RUN mkdir -p data logs pilot/meta_data pilot/data
EXPOSE 5670
HEALTHCHECK --interval=30s --start-period=120s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5670/safe-analysis/', timeout=4)"
ENTRYPOINT ["python", "/app/docker/data-agent/entrypoint.py"]

FROM runtime AS mcp
HEALTHCHECK NONE
CMD ["mcp"]

FROM runtime AS web
CMD ["web"]
