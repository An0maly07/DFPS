# Pre-demo smoke check (PLAN.md §7 checklist): warms every endpoint the dashboard hits and the
# first tiles of each COG, and prints timings. Run against local or deployed URLs:
#   scripts\predemo_check.ps1                                   # local defaults
#   scripts\predemo_check.ps1 -Api https://…modal.run -Web https://….vercel.app
param(
  [string]$Api = "http://127.0.0.1:8000",
  [string]$Web = "http://127.0.0.1:3000",
  [string]$District = "kolhapur-sangli"
)
$ErrorActionPreference = "Continue"
$fail = 0
function Check($name, [scriptblock]$fn) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try { $r = & $fn; $sw.Stop(); "{0,-46} OK  {1,6} ms  {2}" -f $name, $sw.ElapsedMilliseconds, $r }
  catch { $sw.Stop(); $script:fail++; "{0,-46} FAIL {1,6} ms  {2}" -f $name, $sw.ElapsedMilliseconds, $_.Exception.Message }
}

Check "GET /health" { $h = Invoke-RestMethod "$Api/health" -TimeoutSec 30; "model_loaded=$($h.model_loaded) alerts=[$($h.alert_channels -join ',')]" }
Check "GET /api/regions" { $r = Invoke-RestMethod "$Api/api/regions" -TimeoutSec 30; "$($r.features.Count) regions" }
Check "GET /api/forecast" { $f = Invoke-RestMethod "$Api/api/forecast?lat=16.70&lon=74.24" -TimeoutSec 60; "today=$($f.today) q_max3d=$($f.discharge.max_next_3d)" }
Check "GET /api/risk-score/latest" { $r = Invoke-RestMethod "$Api/api/risk-score/latest?district=$District" -TimeoutSec 30; "$($r.alert_level) $([math]::Round($r.risk_score,3)) @ $($r.scored_at)" }
Check "POST /api/risk-score (live features)" { $b = @{ district = $District; persist = $false } | ConvertTo-Json; $r = Invoke-RestMethod "$Api/api/risk-score" -Method Post -Body $b -ContentType "application/json" -TimeoutSec 90; "$($r.alert_level) $([math]::Round($r.risk_score,3))" }
Check "GET /api/drought-index" { $d = Invoke-RestMethod "$Api/api/drought-index?district=$District" -TimeoutSec 60; "$($d.composite.category) spi3=$($d.spi3)" }
Check "GET /api/events/replay maharashtra_2021" { $r = Invoke-RestMethod "$Api/api/events/replay?event=maharashtra_2021" -TimeoutSec 120; "first_orange=$($r.summary.first_orange) lead=$($r.summary.lead_days_orange_before_peak)d" }
Check "GET /api/events/replay marathwada_2018" { $r = Invoke-RestMethod "$Api/api/events/replay?event=marathwada_2018" -TimeoutSec 120; "$($r.summary.months_available) months, worst=$($r.summary.worst_month.month)" }

$sar = $null
Check "GET /api/flood-extent sar_otsu peak" { $script:sar = Invoke-RestMethod "$Api/api/flood-extent?district=$District&date=peak" -TimeoutSec 60; "$($script:sar.captured_at) $($script:sar.flood_area_km2) km2" }
$pri = $null
Check "GET /api/flood-extent prithvi" { $script:pri = Invoke-RestMethod "$Api/api/flood-extent?district=$District&date=peak&source=prithvi" -TimeoutSec 60; "$($script:pri.captured_at) $($script:pri.flood_area_km2) km2 cloud=$($script:pri.model.cloud_pct)%" }

# Warm the tile cache: one z10 tile at the region centre for every tile template returned.
function Tile($tpl, $z, $lon, $lat) {
  $n = [math]::Pow(2, $z)
  $x = [math]::Floor(($lon + 180) / 360 * $n)
  $latr = $lat * [math]::PI / 180
  $y = [math]::Floor((1 - [math]::Log([math]::Tan($latr) + 1 / [math]::Cos($latr)) / [math]::PI) / 2 * $n)
  $tpl.Replace("{z}", "$z").Replace("{x}", "$x").Replace("{y}", "$y")
}
foreach ($set in @(@{ n = "sar"; e = $sar }, @{ n = "prithvi"; e = $pri })) {
  if ($null -eq $set.e) { continue }
  foreach ($p in $set.e.tiles.PSObject.Properties) {
    $u = Tile $p.Value 11 74.24 16.70
    Check "tile $($set.n)/$($p.Name) z11" { $r = Invoke-WebRequest $u -TimeoutSec 60 -UseBasicParsing; "$($r.StatusCode) $($r.Content.Length) bytes" }
  }
}

Check "GET dashboard HTML" { $r = Invoke-WebRequest $Web -TimeoutSec 60 -UseBasicParsing; "$($r.StatusCode) $($r.Content.Length) bytes" }
Check "GET /maplibre/maplibre-gl-worker.mjs" { $r = Invoke-WebRequest "$Web/maplibre/maplibre-gl-worker.mjs" -TimeoutSec 30 -UseBasicParsing; "$($r.StatusCode) $($r.Content.Length) bytes" }

""
if ($fail -eq 0) { "ALL CHECKS PASSED" } else { "$fail CHECK(S) FAILED" }
exit $fail
