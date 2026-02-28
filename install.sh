#!/usr/bin/env bash
# ============================================================================
# CURE — Codebase Update & Refactor Engine
# One-command installer for macOS, Linux, and Windows (WSL)
# ============================================================================
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# What it does (9 steps):
#   1. Detect OS and package manager
#   2. Install Python 3.9+ (if missing)
#   3. Create .venv virtual environment
#   4. Install pip dependencies from requirements.txt
#   5. Install system tools (ccls, lizard, flawfinder)
#   6. Set up .env from env.example (if needed)
#   7. Create output directories
#   8. Validate the installation
#   9. Print launch instructions
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Helpers ─────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}[CURE]${NC} $*"; }
success() { echo -e "${GREEN}[  OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()    { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

step() {
    STEP_NUM=$((${STEP_NUM:-0} + 1))
    echo ""
    echo -e "${BOLD}${BLUE}[$STEP_NUM/9]${NC} ${BOLD}$*${NC}"
    echo -e "${BLUE}$(printf '%.0s─' {1..60})${NC}"
}

# ── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}"
echo "   ╔═══════════════════════════════════════════════════════╗"
echo "   ║                                                       ║"
echo "   ║     CURE — Codebase Update & Refactor Engine          ║"
echo "   ║     Embedded C/C++ Analysis & Repair Pipeline         ║"
echo "   ║                                                       ║"
echo "   ╚═══════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ============================================================================
# Step 1: Detect OS and package manager
# ============================================================================
STEP_NUM=0
step "Detecting operating system"

OS="unknown"
PKG_MGR="none"
IS_WSL=false

if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
    PKG_MGR="brew"
    if ! command -v brew &>/dev/null; then
        warn "Homebrew not found. Installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    success "macOS detected (Homebrew)"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Check if running inside WSL
    if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
        IS_WSL=true
    fi

    if command -v apt-get &>/dev/null; then
        PKG_MGR="apt"
        OS="Linux (Debian/Ubuntu)"
    elif command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
        OS="Linux (Fedora/RHEL)"
    elif command -v yum &>/dev/null; then
        PKG_MGR="yum"
        OS="Linux (CentOS/RHEL)"
    elif command -v pacman &>/dev/null; then
        PKG_MGR="pacman"
        OS="Linux (Arch)"
    else
        fail "No supported package manager found (apt, dnf, yum, pacman)"
    fi

    if $IS_WSL; then
        success "$OS detected (WSL)"
    else
        success "$OS detected"
    fi
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    fail "Native Windows is not supported. Please use WSL (Windows Subsystem for Linux)."
else
    fail "Unsupported OS: $OSTYPE"
fi

# ============================================================================
# Step 2: Install Python 3.9+
# ============================================================================
step "Checking Python 3.9+"

PYTHON_CMD=""

# Find a suitable Python
for cmd in python3 python python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${ver%%.*}"
        minor="${ver#*.}"
        if [[ "$major" -ge 3 && "$minor" -ge 9 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    info "Python 3.9+ not found. Installing..."
    case "$PKG_MGR" in
        brew)   brew install python@3.12 ;;
        apt)    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv ;;
        dnf)    sudo dnf install -y python3 python3-pip ;;
        yum)    sudo yum install -y python3 python3-pip ;;
        pacman) sudo pacman -Sy --noconfirm python python-pip ;;
    esac
    # Re-detect
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON_CMD="$cmd"
            break
        fi
    done
    [[ -z "$PYTHON_CMD" ]] && fail "Python installation failed"
fi

PYTHON_VER=$("$PYTHON_CMD" --version 2>&1)
success "$PYTHON_VER ($PYTHON_CMD)"

# ============================================================================
# Step 3: Create virtual environment
# ============================================================================
step "Creating Python virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
    info "Virtual environment already exists at .venv/"
    success "Reusing existing .venv"
else
    "$PYTHON_CMD" -m venv "$VENV_DIR" || {
        # On some Debian/Ubuntu systems, python3-venv may be missing
        if [[ "$PKG_MGR" == "apt" ]]; then
            warn "python3-venv not available. Installing..."
            sudo apt-get install -y python3-venv
            "$PYTHON_CMD" -m venv "$VENV_DIR"
        else
            fail "Failed to create virtual environment"
        fi
    }
    success "Created .venv/"
fi

# Activate venv
source "$VENV_DIR/bin/activate"
success "Activated virtual environment"

# Upgrade pip
pip install --upgrade pip setuptools wheel --quiet
success "pip upgraded"

# ============================================================================
# Step 4: Install pip dependencies
# ============================================================================
step "Installing Python dependencies"

if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
    fail "requirements.txt not found in project directory"
fi

pip install -r "$SCRIPT_DIR/requirements.txt" --quiet 2>&1 | tail -5 || {
    warn "Some packages had issues. Retrying with verbose output..."
    pip install -r "$SCRIPT_DIR/requirements.txt"
}

success "All Python dependencies installed"

# ============================================================================
# Step 5: Install system tools
# ============================================================================
step "Installing system tools (ccls, lizard, flawfinder)"

# -- ccls (C/C++ language server for AST analysis) --
if command -v ccls &>/dev/null; then
    success "ccls already installed ($(ccls --version 2>&1 | head -1))"
else
    info "Installing ccls..."
    case "$PKG_MGR" in
        brew)   brew install ccls ;;
        apt)    sudo apt-get install -y ccls ;;
        dnf)    sudo dnf install -y ccls ;;
        yum)    warn "ccls not available via yum. Install from source or EPEL." ;;
        pacman) sudo pacman -Sy --noconfirm ccls ;;
    esac
    if command -v ccls &>/dev/null; then
        success "ccls installed"
    else
        warn "ccls not available — CCLS-based adapters will be disabled"
    fi
fi

# -- lizard (cyclomatic complexity) -- installed via pip above
if python -c "import lizard" 2>/dev/null; then
    success "lizard available (pip)"
else
    warn "lizard not importable — AST complexity adapter may be limited"
fi

# -- flawfinder (security scanner) -- installed via pip above
if command -v flawfinder &>/dev/null; then
    success "flawfinder available ($(flawfinder --version 2>&1 | head -1))"
else
    warn "flawfinder not found — security adapter may be limited"
fi

# ============================================================================
# Step 6: Set up .env file
# ============================================================================
step "Setting up environment configuration"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/env.example"

if [[ -f "$ENV_FILE" ]]; then
    success ".env file already exists"
    info "Review .env and update API keys as needed"
elif [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    success "Created .env from env.example"
    warn "Edit .env and add your API keys (QGENIE_API_KEY / LLM_API_KEY)"
else
    # Create a minimal .env — API keys only; everything else in global_config.yaml
    cat > "$ENV_FILE" << 'ENVEOF'
# ── CURE Environment — API Keys Only ────────────────────────────────────────
# All other configuration lives in global_config.yaml

# ── QGenie API Key (required when llm_provider is "qgenie") ────────────────
QGENIE_API_KEY=""

# ── LLM API Key (required when llm_provider is "anthropic" / "azure" etc.) ─
LLM_API_KEY=""
ENVEOF
    success "Created .env with defaults"
    warn "Edit .env and add your LLM_API_KEY (or QGENIE_API_KEY)"
fi

# Also create env.example if missing
if [[ ! -f "$ENV_EXAMPLE" ]]; then
    cat > "$ENV_EXAMPLE" << 'ENVEOF'
# ── CURE Environment — API Keys Only ────────────────────────────────────────
# Copy this file to .env and fill in your values:
#   cp env.example .env
#
# All other configuration (paths, database, LLM models, etc.) lives in
# global_config.yaml — edit that file to customize your environment.

# ── QGenie API Key (required when llm_provider is "qgenie") ────────────────
QGENIE_API_KEY=""

# ── LLM API Key (required when llm_provider is "anthropic" / "azure" etc.) ─
LLM_API_KEY=""
ENVEOF
    success "Created env.example template"
fi

# ============================================================================
# Step 7: Create output directories
# ============================================================================
step "Creating output directories"

mkdir -p "$SCRIPT_DIR/out/patched_files"
mkdir -p "$SCRIPT_DIR/out/hitl"
success "out/ directory structure ready"

# ============================================================================
# Step 8: Validate the installation
# ============================================================================
step "Validating installation"

ERRORS=0

# Check Python imports
info "Testing core imports..."

"$PYTHON_CMD" -c "
import sys
errors = []

# Core
try:
    import openpyxl
except ImportError:
    errors.append('openpyxl')

try:
    import pandas
except ImportError:
    errors.append('pandas')

try:
    import streamlit
except ImportError:
    errors.append('streamlit')

try:
    import rich
except ImportError:
    errors.append('rich')

try:
    import psutil
except ImportError:
    errors.append('psutil')

try:
    import dotenv
except ImportError:
    errors.append('python-dotenv')

try:
    import langgraph
except ImportError:
    errors.append('langgraph')

try:
    import networkx
except ImportError:
    errors.append('networkx')

if errors:
    print(f'MISSING: {', '.join(errors)}')
    sys.exit(1)
else:
    print('OK')
    sys.exit(0)
" && success "All core Python packages importable" || {
    warn "Some packages missing — run: pip install -r requirements.txt"
    ERRORS=$((ERRORS + 1))
}

# Check main.py syntax
python -m py_compile main.py 2>/dev/null && success "main.py compiles OK" || {
    warn "main.py has syntax issues"
    ERRORS=$((ERRORS + 1))
}

# Check fixer_workflow.py syntax
python -m py_compile fixer_workflow.py 2>/dev/null && success "fixer_workflow.py compiles OK" || {
    warn "fixer_workflow.py has syntax issues"
    ERRORS=$((ERRORS + 1))
}

if [[ $ERRORS -eq 0 ]]; then
    success "All validation checks passed!"
else
    warn "$ERRORS validation issue(s) — CURE may still work with limited features"
fi

# ============================================================================
# Step 9: Print launch instructions
# ============================================================================
step "Installation complete!"

echo ""
echo -e "${GREEN}${BOLD}  ╔═════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}  ║         CURE installed successfully!                ║${NC}"
echo -e "${GREEN}${BOLD}  ╚═════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Quick Start:${NC}"
echo ""
echo -e "    ${CYAN}# Activate the virtual environment${NC}"
echo -e "    source .venv/bin/activate"
echo ""
echo -e "    ${CYAN}# Launch the Streamlit dashboard${NC}"
echo -e "    ./launch.sh"
echo ""
echo -e "    ${CYAN}# Or run analysis from CLI${NC}"
echo -e "    python main.py --llm-exclusive --codebase-path /path/to/your/code"
echo ""
echo -e "    ${CYAN}# Analyse a patch file${NC}"
echo -e "    python fixer_workflow.py --batch-patch changes.patch --codebase-path /path/to/code"
echo ""
echo -e "  ${BOLD}Configuration:${NC}"
echo -e "    Edit ${YELLOW}.env${NC} to set your API key (LLM_API_KEY / QGENIE_API_KEY)"
echo -e "    Edit ${YELLOW}global_config.yaml${NC} to customise paths and LLM settings"
echo ""
echo -e "  ${BOLD}Documentation:${NC}"
echo -e "    Open ${YELLOW}index.html${NC} in your browser for the CURE website"
echo -e "    See ${YELLOW}README.md${NC} for full documentation"
echo ""
