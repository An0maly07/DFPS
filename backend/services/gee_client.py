"""Earth Engine wrapper: service-account init + raster download helpers."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import ee
import requests

from backend.config import settings

# High-volume endpoint is the one Google recommends for programmatic/automated access.
EE_HIGH_VOLUME_URL = "https://earthengine-highvolume.googleapis.com"

_lock = threading.Lock()
_initialized = False


def init_ee() -> None:
    """Initialise Earth Engine once per process using the service-account key."""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        key = settings.gee_key_path
        if not key.exists():
            raise FileNotFoundError(
                f"GEE service-account key not found at {key}; set GEE_SERVICE_ACCOUNT_JSON in .env"
            )
        email = json.loads(key.read_text())["client_email"]
        creds = ee.ServiceAccountCredentials(email, str(key))
        ee.Initialize(creds, project=settings.gee_project_id, opt_url=EE_HIGH_VOLUME_URL)
        _initialized = True


def download_geotiff(
    image: ee.Image,
    region: ee.Geometry,
    scale_m: float,
    out_path: Path,
    crs: str = "EPSG:4326",
) -> Path:
    """Synchronously download an image as a plain GeoTIFF via getDownloadURL.

    Subject to Earth Engine's ~50 MB per-request cap, so callers choose scale_m
    to keep (width * height * bytes_per_pixel) under that.
    """
    init_ee()
    url = image.getDownloadURL(
        {"scale": scale_m, "region": region, "crs": crs, "format": "GEO_TIFF"}
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=600) as r:
        if r.status_code != 200:
            raise RuntimeError(f"EE download failed ({r.status_code}): {r.text[:400]}")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return out_path


def region_rectangle(bounds: list[float]) -> ee.Geometry:
    """bounds = [min_lon, min_lat, max_lon, max_lat]."""
    init_ee()
    return ee.Geometry.Rectangle(bounds, proj="EPSG:4326", geodesic=False)
