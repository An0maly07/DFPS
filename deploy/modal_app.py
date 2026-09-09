"""Modal deployment of the Indradhanu API + TiTiler (PLAN.md §7).

Two always-warm web endpoints in one Modal app:
  * api   → FastAPI backend (backend.main:app)
  * tiles → TiTiler serving COGs straight from Supabase Storage

Setup (once):
  pip install modal && modal setup
  modal secret create indradhanu-env \
      SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… SUPABASE_ANON_KEY=… \
      GEE_PROJECT_ID=… GEE_SERVICE_ACCOUNT_JSON_CONTENT="$(cat backend/secrets/<key>.json)" \
      COG_SOURCE=remote INTERNAL_API_TOKEN=<random> \
      CORS_ORIGINS=https://<your-vercel-app>.vercel.app \
      TITILER_URL=https://<workspace>--indradhanu-tiles.modal.run
Deploy:
  modal deploy deploy/modal_app.py
The printed URLs go into Vercel's NEXT_PUBLIC_API_URL (api) and the secret's TITILER_URL (tiles);
re-run the deploy after updating the secret so the API picks up the tiles URL.
"""

from __future__ import annotations

from pathlib import Path

import modal

REPO = Path(__file__).resolve().parent.parent

# Only the files the API needs at runtime: code, model artifacts, small JSON/CSV data.
BACKEND_IGNORE = [
    ".venv", "**/__pycache__", "secrets", "data/cache", "data/cog_exports", "data/prithvi",
    "**/*.tif", "**/*.tiff", "**/*.pth", "**/*.ckpt", "**/*.npz",
]

api_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgomp1")
    .pip_install_from_requirements(str(REPO / "backend" / "requirements.txt"))
    .add_local_dir(str(REPO / "backend"), "/root/backend", ignore=BACKEND_IGNORE)
)

# GDAL settings that make range-reads against Supabase Storage fast (same as scripts/dev-titiler.ps1).
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_CACHEMAX": "512",
    "CPL_VSIL_CURL_CACHE_SIZE": "200000000",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "50000000",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_BAND_BLOCK_CACHE": "HASHSET",
    "GDAL_INGESTED_BYTES_AT_OPEN": "32768",
    "TITILER_API_CORS_ORIGINS": "*",
}
tiles_image = modal.Image.debian_slim(python_version="3.11").pip_install("titiler.application>=0.21").env(GDAL_ENV)

app = modal.App("indradhanu")


@app.function(
    image=api_image,
    secrets=[modal.Secret.from_name("indradhanu-env")],
    min_containers=1,          # no cold start during judging
    timeout=300,
    scaledown_window=600,
)
@modal.concurrent(max_inputs=32)
@modal.asgi_app(label="indradhanu-api")
def api():
    from backend.main import app as fastapi_app

    return fastapi_app


@app.function(image=tiles_image, min_containers=1, timeout=120, scaledown_window=600)
@modal.concurrent(max_inputs=64)
@modal.asgi_app(label="indradhanu-tiles")
def tiles():
    from titiler.application.main import app as titiler_app

    return titiler_app
