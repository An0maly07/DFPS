"""Central settings. Loads .env from the repo root so relative paths in .env resolve
the same way whether uvicorn is launched from the repo root or from backend/."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
COG_DIR = DATA_DIR / "cog_exports"
CACHE_DIR = DATA_DIR / "cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    gee_project_id: str = ""
    gee_service_account_json: str = ""
    # Alternative for hosted deployments (Modal/Cloud Run secrets): the key file's *content*.
    gee_service_account_json_content: str = ""

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""

    titiler_url: str = "http://127.0.0.1:8001"
    # "auto": point TiTiler at the local COG file when it exists (fast on the demo
    # laptop), else at the public Storage URL. "remote": always Storage URLs.
    cog_source: str = "auto"

    default_lat: float = 16.70
    default_lon: float = 74.24

    # API
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    internal_api_token: str = ""   # protects /api/alerts/trigger; empty = open (dev only)

    # Alerting (Phase 2-upgrade)
    firebase_service_account_json: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""   # e.g. whatsapp:+14155238886 (sandbox)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def firebase_key_path(self) -> Path | None:
        if not self.firebase_service_account_json:
            return None
        p = Path(self.firebase_service_account_json)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def gee_key_path(self) -> Path:
        if self.gee_service_account_json_content and not self.gee_service_account_json:
            p = CACHE_DIR / "gee_service_account.json"
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(self.gee_service_account_json_content)
            return p
        p = Path(self.gee_service_account_json)
        return p if p.is_absolute() else REPO_ROOT / p


settings = Settings()

for _d in (COG_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
