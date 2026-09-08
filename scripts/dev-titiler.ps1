# Local TiTiler for the dashboard (port 8001). GDAL caching env vars matter a lot:
# without them every tile re-reads the COG header from Supabase Storage.
$env:GDAL_DISABLE_READDIR_ON_OPEN = "EMPTY_DIR"
$env:GDAL_CACHEMAX = "512"
$env:CPL_VSIL_CURL_CACHE_SIZE = "200000000"
$env:CPL_VSIL_CURL_ALLOWED_EXTENSIONS = ".tif,.tiff"
$env:VSI_CACHE = "TRUE"
$env:VSI_CACHE_SIZE = "50000000"
$env:GDAL_HTTP_MULTIPLEX = "YES"
$env:GDAL_HTTP_VERSION = "2"
$env:GDAL_HTTP_MERGE_CONSECUTIVE_RANGES = "YES"
$env:GDAL_BAND_BLOCK_CACHE = "HASHSET"
$env:GDAL_INGESTED_BYTES_AT_OPEN = "32768"
$env:TITILER_API_CORS_ORIGINS = "*"
Set-Location "$PSScriptRoot\.."
& "backend\.venv\Scripts\python.exe" -m uvicorn titiler.application.main:app --host 127.0.0.1 --port 8001 --workers 2 --log-level warning
