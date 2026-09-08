# Indradhanu API image (Cloud Run / any container host). Build from the repo root:
#   docker build -f backend/Dockerfile -t indradhanu-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# GDAL/PROJ come bundled in the rasterio wheels; only build tooling is needed here.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libgomp1 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Code + model artifacts + small data files (thresholds, replay cache). Secrets are
# injected as environment variables / mounted files, never baked in.
COPY backend/ backend/

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
