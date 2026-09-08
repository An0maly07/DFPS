-- Indradhanu schema — PLAN.md §5, plus indexes and a unique region name.
-- Idempotent: safe to re-run. Applied by backend/scripts/apply_schema.py
-- (or paste into the Supabase SQL editor).

create extension if not exists postgis schema extensions;

create table if not exists regions (
  id serial primary key,
  name text not null unique,
  geom geometry(Polygon, 4326)
);
create index if not exists regions_geom_idx on regions using gist (geom);

create table if not exists flood_extents (
  id serial primary key,
  region_id int references regions(id),
  source text check (source in ('sar_otsu', 'prithvi')),
  captured_at timestamptz,
  cog_url text,
  geom geometry(MultiPolygon, 4326),
  -- pipeline metadata (thresholds, scene ids, area) so the API can explain the layer
  meta jsonb,
  unique (region_id, source, captured_at)
);
create index if not exists flood_extents_region_time_idx on flood_extents (region_id, captured_at desc);
create index if not exists flood_extents_geom_idx on flood_extents using gist (geom);

create table if not exists risk_scores (
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
create index if not exists risk_scores_region_time_idx on risk_scores (region_id, scored_at desc);

create table if not exists drought_index (
  id serial primary key,
  region_id int references regions(id),
  recorded_at date,
  spi float,
  spei float,
  vhi float,
  composite_category text,
  -- full snapshot (SPI-12, VCI/TCI, SMAP anomaly, component categories)
  meta jsonb,
  unique (region_id, recorded_at)
);
alter table drought_index add column if not exists meta jsonb;
create index if not exists drought_index_region_time_idx on drought_index (region_id, recorded_at desc);

create table if not exists alert_subscribers (
  id serial primary key,
  region_id int references regions(id),
  channel text check (channel in ('fcm','whatsapp')),
  identifier text,
  created_at timestamptz default now(),
  unique (channel, identifier)
);

-- PostgREST-callable helpers so the API never needs a direct Postgres connection.
create or replace function flood_extent_geojson(p_id int)
returns json language sql stable as $$
  select ST_AsGeoJSON(geom, 5)::json from flood_extents where id = p_id
$$;

create or replace function regions_geojson()
returns json language sql stable as $$
  select json_build_object(
    'type', 'FeatureCollection',
    'features', coalesce(json_agg(json_build_object(
      'type', 'Feature',
      'id', id,
      'properties', json_build_object('name', name),
      'geometry', ST_AsGeoJSON(geom)::json
    ) order by id), '[]'::json)
  ) from regions
$$;

-- Regions (bounds mirror backend/regions.py).
-- Flood target: Kolhapur–Sangli (PLAN.md §9). Drought validation: Beed, Marathwada.
insert into regions (name, geom)
values
  ('kolhapur-sangli',
   ST_GeomFromText('POLYGON((73.90 16.35, 74.75 16.35, 74.75 17.05, 73.90 17.05, 73.90 16.35))', 4326)),
  ('marathwada-beed',
   ST_GeomFromText('POLYGON((75.30 18.60, 76.30 18.60, 76.30 19.30, 75.30 19.30, 75.30 18.60))', 4326))
on conflict (name) do nothing;
