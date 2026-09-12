# Horizon — Drought & Flood Risk Platform

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
| Sentinel-2 scene for Prithvi | `python -m backend.scripts.export_s2_scene 2021-07-28 --bounds 74.10 16.55 74.75 16.95 --nx 6 --ny 4` | `backend/data/prithvi/s2_*.tif` |
| Prithvi chips + weak labels | `python -m backend.models.prithvi_data` | `backend/data/prithvi/chips_*/` |
| Prithvi fine-tune + ONNX export (GPU) | `python -m backend.models.prithvi_finetune --epochs 12` | `backend/models/artifacts/prithvi/*.onnx`, `finetune_meta.json` |
| Prithvi flood extent (ONNX inference) | `python -m backend.scripts.export_prithvi_extent 2021-07-28 --sar-ref 2021-08-03` | COGs, Storage, `flood_extents` (`source='prithvi'`) |

(Run with `backend\.venv\Scripts\python.exe` from the repo root; `PYTHONPATH` = repo root.)

### Prithvi (Phase 2) prerequisites

```powershell
backend\.venv\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
# base checkpoint (mmseg format) from Hugging Face:
curl.exe -L -o backend\models\artifacts\prithvi\sen1floods11_Prithvi_100M.pth `
  https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-1.0-100M-sen1floods11/resolve/main/sen1floods11_Prithvi_100M.pth
```

The model is re-implemented in plain PyTorch ([backend/models/prithvi_model.py](backend/models/prithvi_model.py)) so the released
Sen1Floods11 checkpoint (backbone + neck + FCN head) loads strictly; TerraTorch would only load the foundation backbone and needs a
C++ toolchain on Windows. The fine-tune is sized for a 4 GB GPU (micro-batch 2, fp16, first 6 blocks frozen).

## Deployment (PLAN.md §7)

| Component | Where | How |
|---|---|---|
| API + TiTiler | Modal | `pip install modal; modal setup; modal secret create horizon-env …; modal deploy deploy/modal_app.py` (see the file header) |
| Frontend | Vercel | import the repo, root `frontend/`, env `NEXT_PUBLIC_API_URL=<Modal api URL>` |
| COGs | Supabase Storage | already uploaded by the export scripts; set `COG_SOURCE=remote` in the Modal secret |

Pre-demo: hit `/health` and `/api/flood-extent?district=kolhapur-sangli&date=peak` on the deployed API ~10 min before judging
(Modal keeps one container warm, but the first TiTiler tile of each COG still reads headers from Storage).

## Honesty notes baked into the product

- Flood extent is **open water seen by SAR**; water under crops or roofs is not detected. Areas are labelled as such.
- Risk-model labels are a **proxy** (GloFAS discharge ≥ 2-year flow within 3 days), not observed floods.
  Every prediction carries `label_source: proxy_glofas_q2yr`; `/api/risk-score/model` exposes the metrics,
  including that 2021 had *no* positive proxy labels yet the model still went Red four days before the peak.
- Alerts report `skipped: not configured` until FCM / Twilio credentials exist — never "sent".
- Prithvi labels are **weak labels** derived from the SAR mask of the nearest later Sentinel-1 pass (6 days after the
  Sentinel-2 scene), with receded/cloudy pixels ignored — not hand-digitised truth. Its extent classes cloud/shadow as
  "not assessable" (grey) instead of guessing. `finetune_meta.json` records zero-shot vs fine-tuned validation IoU.
- MapLibre 6 + Turbopack: the map worker must be served from `frontend/public/maplibre/` (see `MapView.tsx`), otherwise
  GeoJSON layers (region outlines, drought choropleth) silently never render.
