# Horizon API (port 8000). Run from anywhere; .env is read from the repo root.
Set-Location "$PSScriptRoot\.."
$env:PYTHONPATH = (Get-Location).Path
& "backend\.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --log-level info
