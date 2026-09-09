#!/bin/bash
# ============================================================
#  Indradhanu — One-Click Dev Launcher
#  Starts the FastAPI backend + Next.js frontend together.
#  Usage:  ./start.sh
# ============================================================

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_DIR="$ROOT_DIR"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     🌦  Indradhanu Dev Launcher      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── Cleanup on exit ────────────────────────────────────────
cleanup() {
  echo ""
  echo -e "${YELLOW}Shutting down all processes...${NC}"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  echo -e "${GREEN}Done. Goodbye!${NC}"
}
trap cleanup EXIT INT TERM

# ── 1. Start Backend ────────────────────────────────────────
echo -e "${GREEN}▶ Starting FastAPI backend on http://localhost:8000 ...${NC}"
cd "$BACKEND_DIR"

# Use a virtual env if it exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!
echo -e "  Backend PID: ${BACKEND_PID}"

# Give backend a moment to start
sleep 2

# ── 2. Start Frontend ───────────────────────────────────────
echo ""
echo -e "${GREEN}▶ Starting Next.js frontend on http://localhost:3000 ...${NC}"
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
echo -e "  Frontend PID: ${FRONTEND_PID}"

# ── 3. Open browser ─────────────────────────────────────────
sleep 3
echo ""
echo -e "${CYAN}🌐 Opening http://localhost:3000 in your browser...${NC}"
open "http://localhost:3000"

echo ""
echo -e "${YELLOW}Press Ctrl+C to stop all servers.${NC}"
echo ""

# Wait for both
wait "$BACKEND_PID" "$FRONTEND_PID"
