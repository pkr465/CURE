#!/usr/bin/env bash
# ============================================================================
# CURE — Codebase Update & Refactor Engine
# Convenience launcher for the Streamlit dashboard
# ============================================================================
#
# Usage:
#   ./launch.sh                   # Dashboard only (default port 8502)
#   ./launch.sh --website         # Dashboard + open CURE website in browser
#   ./launch.sh --port 8503       # Custom port
#   ./launch.sh --help            # Show usage
#
# The launcher:
#   - Auto-activates the .venv if not already active
#   - Checks that core dependencies are importable
#   - Prints local + network URLs
#   - Starts Streamlit with the correct settings
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Defaults ────────────────────────────────────────────────────────────────
PORT=8502
OPEN_WEBSITE=false
STREAMLIT_APP="ui/app.py"

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            PORT="${2:-8502}"
            shift 2
            ;;
        --website)
            OPEN_WEBSITE=true
            shift
            ;;
        --app)
            STREAMLIT_APP="${2:-ui/app.py}"
            shift 2
            ;;
        --help|-h)
            echo ""
            echo -e "${BOLD}CURE Launcher${NC}"
            echo ""
            echo "Usage: ./launch.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --port PORT     Streamlit port (default: 8502)"
            echo "  --website       Also open index.html in your browser"
            echo "  --app FILE      Streamlit app file (default: ui/app.py)"
            echo "  --help, -h      Show this help message"
            echo ""
            echo "Examples:"
            echo "  ./launch.sh                    # Dashboard on :8502"
            echo "  ./launch.sh --website          # Dashboard + website"
            echo "  ./launch.sh --port 8503        # Custom port"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Run ./launch.sh --help for usage."
            exit 1
            ;;
    esac
done

# ── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}"
echo "   ┌───────────────────────────────────────────────────┐"
echo "   │  CURE — Codebase Update & Refactor Engine         │"
echo "   │  Embedded C/C++ Analysis & Repair Dashboard       │"
echo "   └───────────────────────────────────────────────────┘"
echo -e "${NC}"

# ── Step 1: Activate virtual environment ────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${BLUE}[1/4]${NC} ${BOLD}Activating virtual environment${NC}"
        source "$VENV_DIR/bin/activate"
        echo -e "${GREEN}  OK${NC}  .venv activated"
    else
        echo -e "${YELLOW}[WARN]${NC} No .venv found. Run ${BOLD}./install.sh${NC} first."
        echo -e "${DIM}       Continuing with system Python...${NC}"
    fi
else
    echo -e "${BLUE}[1/4]${NC} ${BOLD}Virtual environment${NC}"
    echo -e "${GREEN}  OK${NC}  Already active (${VIRTUAL_ENV})"
fi

# ── Step 2: Check dependencies ──────────────────────────────────────────────
echo -e "${BLUE}[2/4]${NC} ${BOLD}Checking dependencies${NC}"

MISSING=()

python3 -c "import streamlit" 2>/dev/null || MISSING+=("streamlit")
python3 -c "import pandas" 2>/dev/null || MISSING+=("pandas")
python3 -c "import openpyxl" 2>/dev/null || MISSING+=("openpyxl")
python3 -c "import rich" 2>/dev/null || MISSING+=("rich")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo -e "${YELLOW}  WARN${NC} Missing: ${MISSING[*]}"
    echo -e "${DIM}       Run: pip install -r requirements.txt${NC}"
else
    echo -e "${GREEN}  OK${NC}  All core dependencies available"
fi

# Check Streamlit app file exists
if [[ ! -f "$SCRIPT_DIR/$STREAMLIT_APP" ]]; then
    echo -e "${RED}[FAIL]${NC} Streamlit app not found: $STREAMLIT_APP"
    exit 1
fi

# ── Step 3: Resolve URLs ────────────────────────────────────────────────────
echo -e "${BLUE}[3/4]${NC} ${BOLD}Resolving network addresses${NC}"

LOCAL_URL="http://localhost:${PORT}"

# Try to get network IP
NETWORK_IP=""
if command -v hostname &>/dev/null; then
    NETWORK_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
fi
if [[ -z "$NETWORK_IP" ]] && command -v ifconfig &>/dev/null; then
    NETWORK_IP=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' || true)
fi
if [[ -z "$NETWORK_IP" ]] && command -v ip &>/dev/null; then
    NETWORK_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}' || true)
fi

NETWORK_URL=""
if [[ -n "$NETWORK_IP" ]]; then
    NETWORK_URL="http://${NETWORK_IP}:${PORT}"
fi

echo ""
echo -e "  ${BOLD}Dashboard URLs:${NC}"
echo -e "    Local:    ${CYAN}${BOLD}${LOCAL_URL}${NC}"
if [[ -n "$NETWORK_URL" ]]; then
    echo -e "    Network:  ${CYAN}${NETWORK_URL}${NC}"
fi
echo ""

# ── Step 4: Open website (optional) ────────────────────────────────────────
if $OPEN_WEBSITE; then
    echo -e "${BLUE}[4/4]${NC} ${BOLD}Opening website + starting dashboard${NC}"
    WEBSITE="$SCRIPT_DIR/index.html"
    if [[ -f "$WEBSITE" ]]; then
        # Open in default browser
        if command -v open &>/dev/null; then
            open "$WEBSITE"   # macOS
        elif command -v xdg-open &>/dev/null; then
            xdg-open "$WEBSITE"  # Linux
        elif command -v wslview &>/dev/null; then
            wslview "$WEBSITE"   # WSL
        else
            echo -e "${DIM}  Could not auto-open browser. Open index.html manually.${NC}"
        fi
        echo -e "${GREEN}  OK${NC}  Website opened in browser"
    else
        echo -e "${YELLOW}  WARN${NC} index.html not found"
    fi
else
    echo -e "${BLUE}[4/4]${NC} ${BOLD}Starting dashboard${NC}"
fi

echo -e "${DIM}  Press Ctrl+C to stop${NC}"
echo ""

# ── Launch Streamlit ────────────────────────────────────────────────────────
exec python3 -m streamlit run "$SCRIPT_DIR/$STREAMLIT_APP" \
    --server.port "$PORT" \
    --server.headless true \
    --browser.gatherUsageStats false
