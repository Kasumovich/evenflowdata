FROM python:3.11-slim

# pandoc is optional -- it renders the Situation Report to PDF. The pipeline
# degrades to Markdown-only if it is absent, so this layer can be dropped for
# a smaller image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-geo.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/
COPY data/seed/ ./data/seed/
COPY flows/ ./flows/
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir -e .

ENV ENSO_TRACKER_ROOT=/app \
    ENSO_STORE_DIR=/data/store \
    ENSO_OUTPUT_DIR=/output \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/output"]
EXPOSE 8501

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/enso_tracker/dashboard/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true"]
