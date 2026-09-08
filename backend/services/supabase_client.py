"""Supabase access: DB reads/writes via PostgREST (service role) and COG uploads
to Storage. Schema DDL lives in backend/sql/schema.sql (see scripts/apply_schema.py)."""

from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

import httpx
from supabase import Client, ClientOptions, create_client

from backend.config import settings

COG_BUCKET = "cogs"

_lock = threading.Lock()
_client: Client | None = None

T = TypeVar("T")

# Idle keep-alive connections get closed by Supabase (RemoteProtocolError on next use)
# and the default HTTP/2 client corrupts its stream state when FastAPI's threadpool
# issues requests concurrently (LocalProtocolError: StreamIDTooLowError). HTTP/1.1
# with a pool avoids the latter; one transparent retry covers the former.
_TRANSIENT = (httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ReadError, httpx.ConnectError, httpx.WriteError)


def _retry(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except _TRANSIENT:
        return fn()


def get_client() -> Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                if not settings.supabase_url or not settings.supabase_service_role_key:
                    raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing from .env")
                http = httpx.Client(
                    http2=False,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=20.0),
                )
                _client = create_client(
                    settings.supabase_url,
                    settings.supabase_service_role_key,
                    options=ClientOptions(httpx_client=http),
                )
    return _client


def _table(name: str):
    return get_client().table(name)


# --- regions -------------------------------------------------------------------------

def get_region(name: str) -> dict[str, Any] | None:
    res = _retry(lambda: _table("regions").select("id,name").eq("name", name).limit(1).execute())
    return res.data[0] if res.data else None


def list_regions() -> list[dict[str, Any]]:
    return _retry(lambda: _table("regions").select("id,name").order("name").execute()).data


def regions_geojson() -> dict[str, Any]:
    return _retry(lambda: get_client().rpc("regions_geojson", {}).execute()).data


# --- storage -------------------------------------------------------------------------

def ensure_cog_bucket() -> None:
    client = get_client()
    existing = {b.name for b in _retry(client.storage.list_buckets)}
    if COG_BUCKET not in existing:
        client.storage.create_bucket(COG_BUCKET, options={"public": True})


def cog_public_url(object_name: str) -> str:
    return get_client().storage.from_(COG_BUCKET).get_public_url(object_name)


def upload_cog(local_path: Path, object_name: str | None = None) -> str:
    """Upload a COG to the public bucket and return its public URL."""
    ensure_cog_bucket()
    object_name = object_name or local_path.name
    content_type = mimetypes.guess_type(local_path.name)[0] or "image/tiff"
    storage = get_client().storage.from_(COG_BUCKET)
    data = local_path.read_bytes()
    _retry(lambda: storage.upload(object_name, data, file_options={"content-type": content_type, "upsert": "true"}))
    return storage.get_public_url(object_name)


# --- flood extents -------------------------------------------------------------------

def upsert_flood_extent(
    region_id: int,
    source: str,
    captured_at: str,
    cog_url: str,
    geom_ewkt: str | None,
    meta: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "region_id": region_id,
        "source": source,
        "captured_at": captured_at,
        "cog_url": cog_url,
        "geom": geom_ewkt,
        "meta": meta,
    }
    res = _retry(lambda: _table("flood_extents").upsert(row, on_conflict="region_id,source,captured_at").execute())
    return res.data[0]


def get_flood_extents(region_id: int, source: str | None = None) -> list[dict[str, Any]]:
    def run():
        q = _table("flood_extents").select("id,region_id,source,captured_at,cog_url,meta").eq("region_id", region_id).order("captured_at")
        if source:
            q = q.eq("source", source)
        return q.execute()

    return _retry(run).data


def flood_extent_geojson(extent_id: int) -> dict[str, Any] | None:
    return _retry(lambda: get_client().rpc("flood_extent_geojson", {"p_id": extent_id}).execute()).data


# --- drought ---------------------------------------------------------------------------

def get_drought_rows(region_id: int, limit: int = 60) -> list[dict[str, Any]]:
    return _retry(
        lambda: _table("drought_index").select("*").eq("region_id", region_id).order("recorded_at", desc=True).limit(limit).execute()
    ).data


def upsert_drought_index(row: dict[str, Any]) -> dict[str, Any]:
    return _retry(lambda: _table("drought_index").upsert(row, on_conflict="region_id,recorded_at").execute()).data[0]


# --- risk scores -------------------------------------------------------------------------

def insert_risk_score(row: dict[str, Any]) -> dict[str, Any]:
    return _retry(lambda: _table("risk_scores").insert(row).execute()).data[0]


def latest_risk_score(region_id: int) -> dict[str, Any] | None:
    res = _retry(
        lambda: _table("risk_scores").select("*").eq("region_id", region_id).order("scored_at", desc=True).limit(1).execute()
    )
    return res.data[0] if res.data else None


def risk_score_history(region_id: int, limit: int = 30) -> list[dict[str, Any]]:
    return _retry(
        lambda: _table("risk_scores").select("*").eq("region_id", region_id).order("scored_at", desc=True).limit(limit).execute()
    ).data


# --- alert subscribers ---------------------------------------------------------------------

def upsert_subscriber(region_id: int, channel: str, identifier: str) -> dict[str, Any]:
    row = {"region_id": region_id, "channel": channel, "identifier": identifier}
    return _retry(lambda: _table("alert_subscribers").upsert(row, on_conflict="channel,identifier").execute()).data[0]


def list_subscribers(region_id: int) -> list[dict[str, Any]]:
    return _retry(
        lambda: _table("alert_subscribers").select("id,channel,identifier,created_at").eq("region_id", region_id).order("created_at").execute()
    ).data
