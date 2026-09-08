export type AlertLevel = "green" | "yellow" | "orange" | "red";
export const ALERT_LEVELS: AlertLevel[] = ["green", "yellow", "orange", "red"];

export interface Thresholds {
  q_1_5yr: number;
  q_2yr: number;
  q_5yr: number;
  years: number;
}

export interface Forecast {
  lat: number;
  lon: number;
  region: string;
  today: string;
  weather: {
    dates: string[];
    precipitation_mm: (number | null)[];
    temperature_mean_c: (number | null)[];
    temperature_max_c: (number | null)[];
    soil_moisture_pct: (number | null)[];
    et0_mm: (number | null)[];
  };
  discharge: {
    dates: string[];
    river_discharge: (number | null)[];
    ensemble_mean: (number | null)[];
    ensemble_max: (number | null)[];
    ensemble_min: (number | null)[];
    p25: (number | null)[];
    p75: (number | null)[];
    units: string;
    source: string;
    max_next_3d: number | null;
  };
  thresholds: Thresholds | null;
}

export interface TileSet {
  mask?: string;
  vv_post?: string;
  vv_pre?: string;
}

export interface FloodExtent {
  district: string;
  source: "sar_otsu" | "prithvi";
  captured_at: string;
  event_date: string | null;
  scene_ids: string[] | null;
  cog_url: string;
  urls: Record<string, string>;
  tiles: TileSet;
  bounds: [number, number, number, number] | null;
  flood_area_km2: number | null;
  area_note: string;
  thresholds_db: { post: number | null; pre: number | null; method: string | null };
  classes: Record<string, string>;
  available_dates: string[];
  snapshots: { date: string; flood_area_km2: number | null; event_date: string | null }[];
  /** snapshot with the largest detected flood area — the demo's opening frame */
  peak_date: string;
  geojson?: GeoJSON.MultiPolygon | null;
}

export interface Explanation {
  pushing_up: { feature: string; contribution: number }[];
  pushing_down: { feature: string; contribution: number }[];
}

export interface RiskScoreResponse {
  risk_score: number;
  alert_level: AlertLevel;
  shap_values: Record<string, number>;
  shap_base_value: number;
  shap_units: string;
  features: Record<string, number>;
  label_source: string;
  model_version: string;
  district: string;
  feature_source: string;
  context: { scored_for?: string; discharge_forecast_max3d?: number | null };
  alert_thresholds: Record<AlertLevel, number>;
  explanation: Explanation;
  persisted?: { id: number; scored_at: string };
}

export interface LatestRisk {
  id: number;
  region_id: number;
  scored_at: string;
  rainfall_mm: number | null;
  soil_moisture_pct: number | null;
  temperature_c: number | null;
  discharge_forecast: number | null;
  risk_score: number;
  alert_level: AlertLevel;
  shap_json: {
    values: Record<string, number>;
    base_value: number;
    units: string;
    features: Record<string, number>;
    label_source?: string;
    model_version?: string;
    scored_for?: string;
  } | null;
  district: string;
  explanation: Explanation | null;
  alert_thresholds: Record<AlertLevel, number>;
}

/** Unified shape the banner / SHAP panel consume, whatever the source. */
export interface RiskView {
  level: AlertLevel;
  score: number;
  scoredFor: string;
  storedAt?: string;
  shap: Record<string, number>;
  baseValue: number;
  features: Record<string, number>;
  explanation: Explanation | null;
  labelSource?: string;
  modelVersion?: string;
  source: "stored" | "fresh" | "replay";
}

export interface DroughtIndex {
  district: string;
  recorded_at: string;
  target_month: string | null;
  spi3: number | null;
  spei3: number | null;
  spi12: number | null;
  spei12: number | null;
  spi_through: string | null;
  vhi: number | null;
  vci: number | null;
  tci: number | null;
  vhi_month: string | null;
  soil_moisture: {
    sm_rootzone: number | null;
    sm_z: number | null;
    sm_anomaly_pct: number | null;
    climatology_years: number | null;
  };
  composite: {
    category: string;
    score: number | null;
    component_categories: Record<string, number> | null;
    weights_used: Record<string, number> | null;
    scale: string[];
  };
  series: {
    dates: string[] | null;
    spi3: (number | null)[] | null;
    spi12: (number | null)[] | null;
    precip_mm_last12: (number | null)[] | null;
  };
  sources: Record<string, string>;
}

export interface ReplayExtent {
  id: number;
  cog_url: string;
  urls: Record<string, string>;
  tiles: TileSet;
  flood_area_km2: number | null;
  bounds: [number, number, number, number] | null;
}

export interface ReplayEntry {
  date: string;
  rain_mm: number;
  rain_3d: number;
  upstream_rain_7d: number;
  soil_moisture_pct: number;
  discharge: number;
  risk_score: number;
  alert_level: AlertLevel;
  shap_values: Record<string, number>;
  flood_extent?: ReplayExtent;
}

export interface Replay {
  event: string;
  type: "flood" | "drought";
  region: string;
  title: string;
  start: string;
  end: string;
  documented_peak?: string;
  narrative: string;
  sources: string[];
  model_version?: string;
  label_source?: string;
  summary: {
    glofas_peak_date?: string;
    glofas_peak_discharge?: number;
    first_yellow?: string | null;
    first_orange?: string | null;
    first_red?: string | null;
    lead_days_orange_before_peak?: number | null;
    max_risk_score?: number;
    sar_dates?: string[];
    sar_flood_area_km2?: Record<string, number | null>;
    months_available?: number;
  };
  timeline: ReplayEntry[];
}

export interface RegionFeature {
  type: "Feature";
  id: number;
  properties: { name: string; lat?: number; lon?: number; bounds?: [number, number, number, number] };
  geometry: GeoJSON.Polygon;
}

export interface RegionsFC {
  type: "FeatureCollection";
  features: RegionFeature[];
}

export interface EventsList {
  events: { id: string; type: string; region: string; title: string; start: string; end: string }[];
}

export interface Health {
  status: string;
  model_loaded: boolean;
  titiler_url: string;
  alert_channels: string[];
}
