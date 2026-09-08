# Indradhanu — Drought & Flood Risk Platform

Sentinel-1 SAR flood extent (edge-Otsu) · Open-Meteo / GloFAS forecasts · XGBoost + SHAP risk
scoring · SPI/SPEI/VHI/SMAP drought indices · FastAPI · Supabase/PostGIS · Next.js + MapLibre + TiTiler.
Blueprint: [PLAN.md](PLAN.md).

## Run locally (three terminals)

```powershell
# 1. API  → http://127.0.0.1:8000  (docs at /docs)
scripts\dev-api.ps1

# 2. TiTiler tile server → http://127.0.0.1:8001
scripts\dev-titiler.ps1

# 3. Dashboard → http://127.0.0.1:3000
cd frontend; npm run dev -- --hostname 127.0.0.1
```

Open **http://127.0.0.1:3000** (not `localhost` — on Windows it resolves to IPv6 first and every request
waits ~2 s before falling back).

## First-time setup

```powershell
# Python 3.11 backend
py -3.11 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
copy .env.example .env            # fill in GEE + Supabase values
backend\.venv\Scripts\python.exe -m backend.scripts.apply_schema   # creates tables + regions

# Frontend
cd frontend; npm install; copy .env.example .env.local
```

Credentials (see `.env.example`): a Google Earth Engine service-account key registered on an
EE-enabled Cloud project (roles: *Earth Engine Resource Writer* + *Service Usage Consumer*), and a
Supabase project (URL, service-role key, and the direct Postgres URL for `apply_schema`).

## Data pipelines

| Step | Command | Writes |
|---|---|---|
| SAR flood extent for a date | `python -m backend.scripts.export_flood_extent 2021-07-23` | COGs in `backend/data/cog_exports`, Supabase Storage `cogs/`, `flood_extents` |
| Training set (ERA5 + GloFAS, 2010→) | `python -m backend.scripts.build_training_set` | `backend/data/training/*.csv` |
| Train risk model + score today | `python -m backend.scripts.train_risk_model --score-today --write` | `backend/models/artifacts/`, `risk_scores` |
| Drought snapshot | `python -m backend.scripts.compute_drought_index --region marathwada-beed --month 2018-10 --write` | `drought_index` |

(Run with `backend\.venv\Scripts\python.exe` from the repo root; `PYTHONPATH` = repo root.)

## Honesty notes baked into the product

- Flood extent is **open water seen by SAR**; water under crops or roofs is not detected. Areas are labelled as such.
- Risk-model labels are a **proxy** (GloFAS discharge ≥ 2-year flow within 3 days), not observed floods.
  Every prediction carries `label_source: proxy_glofas_q2yr`; `/api/risk-score/model` exposes the metrics,
  including that 2021 had *no* positive proxy labels yet the model still went Red four days before the peak.
- Alerts report `skipped: not configured` until FCM / Twilio credentials exist — never "sent".
