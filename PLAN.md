2# PLAN.md — Drought & Flood Risk Prediction Platform
### Indradhanu 2026 — End-to-End Live Demo Blueprint

**Target region:** Maharashtra (recommend Kolhapur–Sangli or Marathwada for validation against a real historical event)
**Team size assumed:** 4 (roles split below — adjust to your actual headcount)
**Compute:** Local GPU available for fine-tuning
**Demo type:** Live, working, interactive dashboard

---

## 0. One-Page Summary

| Layer | Choice | Why |
|---|---|---|
| Flood detection (imagery) | Sentinel-1 SAR + Otsu (baseline) → Prithvi-EO-2.0 Sen1Floods11 fine-tune (upgrade) | SAR sees through monsoon cloud; Prithvi beats scratch U-Net |
| Flood forecast (river) | Open-Meteo GloFAS API (+ Google Flood Hub if access granted) | Don't retrain what Google already trained on 5,680 gauges |
| Risk scoring | XGBoost + SHAP | Fast, explainable, reliable in live demos |
| Drought | SPI/SPEI (CHIRPS/IMD) + VHI (MODIS) + SMAP soil moisture | Most teams skip this — free differentiation |
| Backend | FastAPI (Python) | ML-serving, GEE calls, model inference |
| Database | Supabase (Postgres + PostGIS) | Spatial queries, auth, storage, realtime |
| Frontend | Next.js + MapLibre GL JS + Recharts | Raster overlays, time-slider, charts |
| Raster serving | COG + TiTiler | Dynamic map tiles without static image hacks |
| Deployment | Vercel (frontend) + Modal or HF ZeroGPU (ML inference) | Free, reliable during judging |
| Alerting | Firebase FCM or Twilio WhatsApp Sandbox | Early-warning needs delivery, not just a dashboard |

**Kill list (do not build):** standalone LSTM, ResNet tile classifier, Random Forest ensemble layer, from-scratch U-Net.

---

## 1. Architecture

```
                        ┌─────────────────────────┐
                        │   Data Sources           │
                        │  • Sentinel-1/2 (GEE)     │
                        │  • Open-Meteo + GloFAS    │
                        │  • CHIRPS / MODIS (GEE)   │
                        │  • SMAP (GEE)             │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │   Ingestion / ETL layer   │
                        │  (Python scripts, cron    │
                        │   or Prefect flow)        │
                        │  → exports COG to storage │
                        └────────────┬─────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                     │
      ┌─────────▼────────┐ ┌────────▼─────────┐ ┌────────▼─────────┐
      │ Flood extent      │ │ Risk engine       │ │ Drought index     │
      │ (Otsu SAR /       │ │ (XGBoost + SHAP)  │ │ (SPI/SPEI/VHI)    │
      │  Prithvi model)   │ │                   │ │                   │
      └─────────┬────────┘ └────────┬─────────┘ └────────┬─────────┘
                │                    │                     │
                └────────────────────┼────────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │   FastAPI backend         │
                        │  /api/flood-extent         │
                        │  /api/risk-score            │
                        │  /api/drought-index         │
                        │  /api/forecast               │
                        │  /api/alerts                 │
                        └────────────┬─────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                     │
      ┌─────────▼────────┐ ┌────────▼─────────┐ ┌────────▼─────────┐
      │ Supabase          │ │ TiTiler            │ │ Alerting          │
      │ (Postgres+PostGIS)│ │ (COG → map tiles)  │ │ (FCM / WhatsApp)  │
      └───────────────────┘ └───────────────────┘ └───────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │   Next.js frontend         │
                        │  MapLibre GL JS map        │
                        │  Recharts forecast charts   │
                        │  Alert-level banner          │
                        │  Time-slider for flood extent│
                        └───────────────────────────┘
```

---

## 2. Data Sources — Setup Checklist

| Source | Purpose | Access | Setup steps |
|---|---|---|---|
| **Google Earth Engine** | Sentinel-1/2, MODIS, CHIRPS, SMAP | Free, noncommercial | Register Cloud project with institutional email → enable Earth Engine API → `pip install earthengine-api` → `ee.Authenticate()` |
| **Open-Meteo** | Rainfall, temperature, GloFAS river discharge | Free, no key | Just call `https://api.open-meteo.com/v1/forecast` and `https://flood-api.open-meteo.com/v1/flood` |
| **Copernicus Data Space** | Raw Sentinel-1/2 scenes (if not using GEE directly) | Free registration | `https://dataspace.copernicus.eu` |
| **NASA POWER** | Backup meteorology | Free, no key | REST API, no auth |
| **Google Flood Hub API** | Operational LSTM river forecasts (5,680+ gauges) | Free, pilot waitlist | Apply now at `support.google.com/flood-hub` — **do not block critical path on this** |
| **IMD / India-WRIS / Bhuvan** | India-specific rainfall/drought/river data | Free but slow/manual | Budget time for manual download; do not assume live API |
| **Sen1Floods11 dataset** | Fine-tuning data for Prithvi | Free, open | `github.com/cloudtostreet/Sen1Floods11` |
| **Drought Atlas of India (SPEI, 1901–2021)** | Historical drought validation | Free download | Search "IITM Drought Atlas India SPEI" |

**Action item (Day 1):** Every team member registers for GEE + Copernicus Data Space accounts today — approval can lag.

---

## 3. Model Plan

### 3.1 Flood extent — SAR + Otsu (baseline, Phase 1)
- Pull one Sentinel-1 GRD scene (VV + VH) over target district for a known flood date, and one pre-flood reference scene.
- Compute backscatter difference or direct VV threshold.
- Apply **Otsu's method** (`skimage.filters.threshold_otsu` or GEE's built-in) to auto-threshold water vs land.
- Export as Cloud-Optimized GeoTIFF (COG).
- **Validated accuracy reference:** Otsu-SAR flood mapping achieved ~94% overall accuracy on the 2018 Kerala floods (published, GEE-based) — cite this in your pitch.

### 3.2 Flood extent — Prithvi fine-tune (upgrade, Phase 2)
- Base model: `ibm-nasa-geospatial/Prithvi-EO-1.0-100M-sen1floods11` (Hugging Face, already fine-tuned on Sen1Floods11 — you are further fine-tuning on your own regional scene, not training from scratch).
- Use **TerraTorch** or the HF `transformers` pipeline.
- Fine-tune ~1 hour on your GPU using a labeled regional flood scene (label a handful of chips yourself using QGIS if no ready label exists).
- Export to ONNX for fast inference in the FastAPI service.
- **Show side-by-side with SAR** in the demo: "optical model vs SAR under cloud cover" — this contrast is your strongest visual moment.

### 3.3 River / flood forecast — API-based (do not train)
- Primary: **Open-Meteo Flood API** (GloFAS-based, free, no key) → 7-day+ discharge forecast.
- Stretch: **Google Flood Hub API**, if pilot access is granted before the event.
- Never build your own LSTM for this — Google's operational model is trained on 5,680 global gauges; you cannot beat it in a hackathon window.

### 3.4 Risk scoring — XGBoost
- **Features:** daily rainfall (mm), soil moisture (%), temperature (°C), river discharge forecast, upstream rainfall accumulation (7-day rolling).
- **Target:** binary or graded flood/drought risk (train on historical labeled events if available, or use a rule-derived proxy target if no labels exist — be transparent about this in the pitch).
- **Output:** risk probability → mapped to Green/Yellow/Orange/Red via fixed thresholds (not a second ensemble model).
- **Add SHAP** (`shap.TreeExplainer`) for a "why this alert level" panel — cheap to add, high judge impact.

### 3.5 Drought index pipeline
- **SPI/SPEI:** compute from CHIRPS rainfall using the `climate-indices` Python package (pip installable), or IMD gridded rainfall if accessible.
- **VHI (Vegetation Health Index):** pull NDVI + LST from MODIS in GEE, compute VCI + TCI → VHI.
- **Soil moisture:** SMAP via GEE (`NASA_USDA/HSL/SMAP10KM_soil_moisture` or similar collection).
- Combine into a simple **Combined Drought Indicator**: weighted average or majority-vote across SPI/VHI/soil-moisture categorical bands.

---

## 4. Backend — FastAPI

### 4.1 Directory structure
```
backend/
├── main.py                  # FastAPI app entrypoint
├── routers/
│   ├── flood.py             # /api/flood-extent, /api/forecast
│   ├── drought.py           # /api/drought-index
│   ├── risk.py              # /api/risk-score
│   └── alerts.py            # /api/alerts
├── models/
│   ├── xgboost_risk.py      # load + predict
│   ├── prithvi_infer.py     # ONNX inference wrapper
│   └── sar_otsu.py          # GEE SAR + Otsu pipeline
├── services/
│   ├── open_meteo.py        # API client
│   ├── gee_client.py        # Earth Engine wrapper
│   └── supabase_client.py   # DB writes
├── data/
│   └── cog_exports/         # local cache of COGs before upload
├── requirements.txt
└── Dockerfile
```

### 4.2 Core endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/forecast?lat=&lon=` | GET | Rainfall + GloFAS discharge forecast (Open-Meteo passthrough) |
| `/api/flood-extent?district=&date=` | GET | Returns COG URL / GeoJSON for flood extent (SAR or Prithvi) |
| `/api/risk-score` | POST | Body: features → returns {risk_score, alert_level, shap_values} |
| `/api/drought-index?district=` | GET | Returns SPI/SPEI/VHI values + composite drought category |
| `/api/alerts/subscribe` | POST | Register device token (FCM) or phone (WhatsApp) |
| `/api/alerts/trigger` | POST (internal) | Fires notification when risk_score crosses threshold |
| `/api/events/replay?event=maharashtra_2021` | GET | Returns pre-computed data for historical validation demo |

### 4.3 Key libraries
```
fastapi
uvicorn
earthengine-api
requests            # Open-Meteo calls
xgboost
shap
scikit-image        # Otsu threshold
rasterio             # COG read/write
onnxruntime          # Prithvi inference
supabase-py
```

---

## 5. Database — Supabase / PostGIS Schema

```sql
-- Enable PostGIS
create extension if not exists postgis schema extensions;

-- Districts / regions of interest
create table regions (
  id serial primary key,
  name text not null,
  geom geometry(Polygon, 4326)
);

-- Flood extent snapshots
create table flood_extents (
  id serial primary key,
  region_id int references regions(id),
  source text check (source in ('sar_otsu', 'prithvi')),
  captured_at timestamptz,
  cog_url text,
  geom geometry(MultiPolygon, 4326)
);

-- Risk scores (time series)
create table risk_scores (
  id serial primary key,
  region_id int references regions(id),
  scored_at timestamptz default now(),
  rainfall_mm float,
  soil_moisture_pct float,
  temperature_c float,
  discharge_forecast float,
  risk_score float,
  alert_level text check (alert_level in ('green','yellow','orange','red')),
  shap_json jsonb
);

-- Drought index snapshots
create table drought_index (
  id serial primary key,
  region_id int references regions(id),
  recorded_at date,
  spi float,
  spei float,
  vhi float,
  composite_category text
);

-- Alert subscriptions
create table alert_subscribers (
  id serial primary key,
  region_id int references regions(id),
  channel text check (channel in ('fcm','whatsapp')),
  identifier text,   -- device token or phone number
  created_at timestamptz default now()
);
```

**Spatial query example** (find risk scores within 10km of a point):
```sql
select rs.* from risk_scores rs
join regions r on rs.region_id = r.id
where ST_DWithin(r.geom, ST_MakePoint(74.24, 16.70)::geography, 10000);
```

---

## 6. Frontend — Next.js + MapLibre

### 6.1 Directory structure
```
frontend/
├── app/
│   ├── page.tsx              # main dashboard
│   ├── layout.tsx
│   └── api/                  # thin proxy routes to FastAPI (optional)
├── components/
│   ├── MapView.tsx            # MapLibre GL JS map, dynamic import (SSR off)
│   ├── FloodOverlay.tsx       # TiTiler tile layer for flood COG
│   ├── DroughtLayer.tsx       # drought raster/choropleth
│   ├── AlertBanner.tsx        # Green/Yellow/Orange/Red banner
│   ├── ForecastChart.tsx      # Recharts line chart (rainfall/discharge)
│   ├── ShapPanel.tsx          # "why this alert" explainability panel
│   ├── TimeSlider.tsx         # scrub through historical flood extent
│   └── ReplayEventSelector.tsx # dropdown: "Replay 2021 Maharashtra flood"
├── lib/
│   └── apiClient.ts
└── package.json
```

### 6.2 Key implementation notes
- **MapLibre import:** use `next/dynamic` with `ssr: false` — MapLibre needs `window`.
- **Flood raster display:** point MapLibre's raster source at your TiTiler endpoint (`/cog/tiles/{z}/{x}/{y}?url=<COG_URL>`), not a static image.
- **Time-slider:** swap the TiTiler COG URL as the user scrubs dates; debounce requests.
- **Alert banner:** poll `/api/risk-score` every N seconds during live demo, or use Supabase Realtime channel for push updates.
- **Charts:** Recharts line chart for rainfall/discharge forecast; overlay a horizontal threshold line for alert level.

---

## 7. Deployment Plan

| Component | Platform | Notes |
|---|---|---|
| Frontend (Next.js) | **Vercel** (Hobby, free) | Connect GitHub repo, auto-deploy on push |
| ML inference service (Prithvi, XGBoost) | **Modal** (free $30/mo credit) or **Hugging Face Spaces + ZeroGPU** (if wrapped in Gradio) | Modal better for a pure API; HF better if you want a standalone Gradio demo too |
| FastAPI backend (if separate from ML service) | **Modal** or **Google Cloud Run** (always-free CPU tier) | Avoid Render free tier — 15-min idle spin-down risks a cold-start mid-demo |
| Database | **Supabase** (free tier) | Postgres + PostGIS + Auth + Storage in one |
| Raster tiles | **TiTiler** — deploy alongside backend or as a separate Modal/Cloud Run service | Point it at COGs stored in Supabase Storage or GCS |
| Alerting | **Firebase** (FCM, free) and/or **Twilio** (WhatsApp Sandbox, free trial) | No SMS — TRAI DLT registration is too slow for a hackathon |

**Pre-demo-day checklist:**
- [ ] Warm up the Modal/HF inference service ~10 min before judging (avoid cold start)
- [ ] Pre-export all GEE-derived COGs — do not run live GEE compute during judging (noncommercial quota risk)
- [ ] Record a fallback demo video in case live APIs hiccup
- [ ] Confirm Supabase free-tier connection limits are not exceeded by simultaneous team testing

---

## 8. Alerting Setup

**Option A — Firebase Cloud Messaging (recommended default):**
1. Create Firebase project → enable Cloud Messaging.
2. Frontend requests notification permission, registers device token.
3. Backend stores token in `alert_subscribers`, calls FCM HTTP v1 API when `risk_score` crosses Orange/Red.

**Option B — Twilio WhatsApp Sandbox (more visually dramatic for judges):**
1. Join Twilio's WhatsApp sandbox with the shared number + join code.
2. Backend calls Twilio API to send a WhatsApp message to a judge's/team's phone when alert fires.
3. **Trial limit:** ~100 free messages — enough for a demo, not production.

**Recommendation:** Implement FCM as the "real" pipeline; use Twilio WhatsApp as a live, visible "watch this phone" demo moment.

---

## 9. Validation / Demo Script

**Goal:** Prove the system isn't just a toy — replay a real event.

1. Pick a documented event: **2021 Maharashtra floods (Kolhapur–Sangli, July 2021)** or **Marathwada drought**.
2. Pre-fetch Sentinel-1 scenes and rainfall data for the days leading up to and during the event.
3. Pre-compute flood extent (SAR + Prithvi) and risk scores for that date range, store in Supabase.
4. Build a `ReplayEventSelector` dropdown in the frontend: selecting "2021 Maharashtra Flood" loads the pre-computed timeline into the `TimeSlider` and `ForecastChart`.
5. **Narrative for judges:** "On July 22, our system would have flagged Orange three days before the peak; here's the SAR-confirmed extent on the day of the flood."

This is your single highest-credibility moment — do not skip it even under time pressure.

---

## 10. Team Task Allocation (4-person split — adjust to your team)

| Role | Owns | Key deliverables |
|---|---|---|
| **A — Geospatial/ML** | SAR+Otsu pipeline, Prithvi fine-tune, drought indices | Flood extent COGs, drought index values, fine-tuned model |
| **B — Backend** | FastAPI, XGBoost risk engine, SHAP, API integration (Open-Meteo, GEE) | All `/api/*` endpoints working end-to-end |
| **C — Frontend** | Next.js dashboard, MapLibre, TiTiler integration, charts | Live dashboard rendering real data |
| **D — Data/Infra** | Supabase schema, deployment (Vercel/Modal), alerting, historical event dataset prep | Deployed, demo-ready environment + replay dataset |

Daily sync recommended: 15 min standup to catch integration mismatches early (e.g., COG URL format, API response schema).

---

## 11. Day-by-Day Build Order

### Days 1–2 (Core plumbing)
- [ ] All accounts set up (GEE, Copernicus, Supabase, Vercel, Modal/HF, Firebase/Twilio)
- [ ] Open-Meteo + GloFAS API calls working, returning JSON
- [ ] One Sentinel-1 SAR scene pulled + Otsu threshold applied in GEE, exported as COG
- [ ] Supabase schema created and reachable from FastAPI

### Days 3–4 (Core models)
- [ ] XGBoost risk model trained on assembled features (even if labels are proxy/synthetic — document this honestly)
- [ ] SHAP explainability wired in
- [ ] SPI/SPEI + VHI + SMAP pipeline computing drought category for target district
- [ ] FastAPI endpoints for flood-extent, risk-score, drought-index all returning real data

### Days 5–6 (Frontend integration)
- [ ] MapLibre map rendering flood COG via TiTiler
- [ ] Drought layer rendering
- [ ] Forecast chart wired to `/api/forecast`
- [ ] Alert banner wired to `/api/risk-score`
- [ ] **Phase 1 demo should be fully working by end of Day 6** — this is your guaranteed fallback

### Days 7–8 (Upgrade — Phase 2)
- [ ] Prithvi fine-tune on GPU, ONNX export, wired into backend as an alternate flood-extent source
- [ ] Side-by-side SAR vs Prithvi comparison view in frontend
- [ ] One alerting channel (FCM or WhatsApp) firing live

### Days 9–10 (Validation + polish)
- [ ] Historical event replay (2021 Maharashtra flood or Marathwada drought) fully working
- [ ] Record fallback demo video
- [ ] Deploy final versions, warm up inference service before judging
- [ ] Rehearse the pitch narrative end-to-end at least twice

*(Adjust day counts to your actual hackathon duration — this assumes roughly a 7–10 day build window; compress proportionally for a 24–48hr sprint by cutting Phase 2 items first.)*

---

## 12. Risk Register

| Risk | Mitigation |
|---|---|
| GEE noncommercial quota throttling mid-build | Pre-export all heavy products early; cache locally |
| Google Flood Hub API access not granted in time | Open-Meteo GloFAS is the guaranteed fallback — never block on Flood Hub |
| Cloud cover blocks optical imagery for target date | This is *why* you have SAR — always have a SAR path ready |
| Prithvi fine-tune underperforms / runs out of time | SAR+Otsu alone is a complete, defensible flood-detection story — ship it even without Prithvi |
| Render/Fly.io cold starts during judging | Use Modal or Cloud Run instead; warm up before judging |
| India government data portals (IMD/WRIS/Bhuvan) unreachable | Treat as manual download only; never assume live API |
| SMS alerting blocked by TRAI DLT | Use FCM push or WhatsApp Sandbox instead |
| Team builds 5 things and finishes 0 | Follow this build order strictly; Phase 1 is the non-negotiable floor |

---

## 13. Pitch Narrative Outline (for the final presentation)

1. **Problem:** Floods and droughts in Maharashtra cause [X] — most existing systems use only optical imagery, which fails under monsoon cloud.
2. **Our approach:** SAR-based cloud-penetrating flood detection + operational-grade forecasting (Open-Meteo/GloFAS, optionally Flood Hub) + an underserved drought track.
3. **Live demo:** dashboard walkthrough — pick district, show flood extent (SAR vs Prithvi), show risk score + SHAP explanation, show drought category, trigger a live alert.
4. **Validation:** replay of the 2021 Maharashtra flood / Marathwada drought — "here's what we would have predicted."
5. **Differentiators:** SAR, geospatial foundation model, explainability, real alerting, historical validation — name each explicitly.
6. **What we'd add next:** Google Flood Hub integration at scale, conformal prediction intervals, expansion beyond Maharashtra.

---

**End of PLAN.md** — treat Section 11 (Day-by-Day Build Order) as the operational source of truth; everything else is reference detail to pull from as you build.
