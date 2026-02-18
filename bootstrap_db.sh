#!/usr/bin/env bash
# ============================================================================
# CURE — Codebase Update & Refactor Engine
# One-step PostgreSQL bootstrap script
#
# Installs PostgreSQL + pgvector, creates the application user, database,
# extension, and grants all required permissions.
#
# Usage:
#   chmod +x bootstrap_db.sh
#   sudo ./bootstrap_db.sh
#
# Defaults match global_config.yaml. Override with environment variables:
#   DB_USER, DB_PASSWORD, DB_NAME, DB_HOST, DB_PORT
# ============================================================================

set -euo pipefail

# ---------- Configurable defaults (match global_config.yaml) ----------
DB_USER="${DB_USER:-codebase_analytics_user}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-codebase_analytics_db}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# ---------- Colors for output ----------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ---------- Detect OS ----------
OS_TYPE="linux"
if [[ "$(uname -s)" == "Darwin" ]]; then
    OS_TYPE="macos"
fi

# ---------- Detect the real (non-root) user ----------
# When run via sudo, $USER / $(whoami) is "root". SUDO_USER holds the original user.
REAL_USER="${SUDO_USER:-$(whoami)}"

# ---------- Step 1: Install PostgreSQL + pgvector ----------
info "Installing PostgreSQL and pgvector extension..."

if [[ "$OS_TYPE" == "macos" ]]; then
    # macOS — use Homebrew. brew must NOT run as root, so drop to the real user.
    if ! command -v brew &>/dev/null; then
        warn "Homebrew not found. Install it from https://brew.sh and re-run."
        exit 1
    fi

    BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
    BREW_CMD="brew"
    if [[ "$(whoami)" == "root" && -n "${REAL_USER}" ]]; then
        BREW_CMD="sudo -u ${REAL_USER} brew"
    fi

    # Detect which PostgreSQL version is installed (prefer @16, fall back to @17, @18)
    PG_VER=""
    for v in 16 17 18; do
        if [[ -d "${BREW_PREFIX}/opt/postgresql@${v}" ]]; then
            PG_VER="$v"
            break
        fi
    done

    # Install PostgreSQL if not found
    if [[ -z "$PG_VER" ]]; then
        PG_VER="16"
        info "Installing PostgreSQL@${PG_VER}..."
        $BREW_CMD install "postgresql@${PG_VER}" 2>/dev/null || true
    fi

    info "Using PostgreSQL@${PG_VER}"
    $BREW_CMD services start "postgresql@${PG_VER}" 2>/dev/null || true

    # Ensure the brew-installed psql is on PATH (Apple Silicon + Intel)
    export PATH="${BREW_PREFIX}/opt/postgresql@${PG_VER}/bin:$PATH"
    PG_CONFIG="${BREW_PREFIX}/opt/postgresql@${PG_VER}/bin/pg_config"

    # Install pgvector — try brew bottle first, fall back to building from source
    PG_EXT_DIR="${BREW_PREFIX}/opt/postgresql@${PG_VER}/share/postgresql@${PG_VER}/extension"

    if [[ ! -f "${PG_EXT_DIR}/vector.control" ]]; then
        # Try brew install first
        $BREW_CMD install pgvector 2>/dev/null || true

        # Check if brew bottle had matching PG version files
        PGVEC_CELLAR="${BREW_PREFIX}/Cellar/pgvector"
        PGVEC_FOUND=false
        if [[ -d "$PGVEC_CELLAR" ]]; then
            PGVEC_VER=$(ls -1 "$PGVEC_CELLAR" | sort -V | tail -1)
            PGVEC_EXT="${PGVEC_CELLAR}/${PGVEC_VER}/share/postgresql@${PG_VER}/extension"
            PGVEC_LIB="${PGVEC_CELLAR}/${PGVEC_VER}/lib/postgresql@${PG_VER}"
            if [[ -d "$PGVEC_EXT" ]]; then
                info "Symlinking pgvector extension files into PostgreSQL@${PG_VER}..."
                PG_LIB_DIR="${BREW_PREFIX}/opt/postgresql@${PG_VER}/lib/postgresql@${PG_VER}"
                ln -sf "${PGVEC_EXT}"/vector* "${PG_EXT_DIR}/" 2>/dev/null || true
                [[ -d "$PGVEC_LIB" ]] && ln -sf "${PGVEC_LIB}"/vector* "${PG_LIB_DIR}/" 2>/dev/null || true
                PGVEC_FOUND=true
            fi
        fi

        # If brew bottle didn't cover this PG version, build from source
        if [[ "$PGVEC_FOUND" == "false" && ! -f "${PG_EXT_DIR}/vector.control" ]]; then
            warn "Homebrew pgvector bottle does not support PostgreSQL@${PG_VER}. Building from source..."
            if ! command -v make &>/dev/null; then
                $BREW_CMD install make 2>/dev/null || true
            fi
            PGVEC_SRC="/tmp/pgvector-build-$$"
            git clone --branch v0.8.1 --depth 1 https://github.com/pgvector/pgvector.git "$PGVEC_SRC" 2>/dev/null
            if [[ -d "$PGVEC_SRC" ]]; then
                (cd "$PGVEC_SRC" && PG_CONFIG="$PG_CONFIG" make -j"$(sysctl -n hw.ncpu)" && make install)
                rm -rf "$PGVEC_SRC"
                info "pgvector built and installed from source."
            else
                warn "Failed to clone pgvector. Install it manually: https://github.com/pgvector/pgvector"
            fi
        fi
    else
        info "pgvector extension already installed."
    fi
elif command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq postgresql postgresql-client postgresql-16-pgvector
elif command -v dnf &>/dev/null; then
    dnf install -y postgresql-server postgresql-contrib pgvector
    postgresql-setup --initdb 2>/dev/null || true
elif command -v yum &>/dev/null; then
    yum install -y postgresql-server postgresql-contrib pgvector
    postgresql-setup initdb 2>/dev/null || true
else
    warn "Unsupported package manager. Please install PostgreSQL and pgvector manually."
    exit 1
fi

# Ensure PostgreSQL is running (Linux)
if [[ "$OS_TYPE" == "linux" ]]; then
    systemctl start postgresql 2>/dev/null || service postgresql start 2>/dev/null || true
    systemctl enable postgresql 2>/dev/null || true
fi
info "PostgreSQL is running."

# ---------- Step 2: Create user, database, extension, and permissions ----------
info "Setting up database '${DB_NAME}' with user '${DB_USER}'..."

# On macOS with Homebrew, the superuser is the real (non-root) user.
# On Linux, the PostgreSQL superuser is the 'postgres' OS user.
if [[ "$OS_TYPE" == "macos" ]]; then
    PSQL_CMD="psql -U ${REAL_USER} -d postgres"
else
    PSQL_CMD="sudo -u postgres psql"
fi

# Step 2a: Create user and database
$PSQL_CMD -v ON_ERROR_STOP=1 <<SQL
-- Create application user (skip if exists)
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;

-- Create database (skip if exists)
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL

# Step 2b: Install pgvector extension (optional — non-fatal if not available)
if [[ "$OS_TYPE" == "macos" ]]; then
    VEC_PSQL="psql -U ${REAL_USER} -d ${DB_NAME}"
else
    VEC_PSQL="sudo -u postgres psql -d ${DB_NAME}"
fi
$VEC_PSQL -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null && \
    info "pgvector extension enabled." || \
    warn "pgvector extension not available — vector DB features will be unavailable. Core features (telemetry, analysis, fixer) are unaffected."

# Step 2c: Grants and default privileges (must always run)
if [[ "$OS_TYPE" == "macos" ]]; then
    GRANT_PSQL="psql -U ${REAL_USER} -d ${DB_NAME}"
else
    GRANT_PSQL="sudo -u postgres psql -d ${DB_NAME}"
fi
$GRANT_PSQL -v ON_ERROR_STOP=1 <<SQL
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
GRANT USAGE, CREATE ON SCHEMA public TO ${DB_USER};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ${DB_USER};
SQL

info "Bootstrap complete!"
info "  Database : ${DB_NAME}"
info "  User     : ${DB_USER}"
info "  Host     : ${DB_HOST}:${DB_PORT}"
info ""
info "Connection string: postgresql+psycopg2://${DB_USER}:****@${DB_HOST}:${DB_PORT}/${DB_NAME}"
