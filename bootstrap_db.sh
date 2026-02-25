#!/usr/bin/env bash
# ============================================================================
# CURE — Codebase Update & Refactor Engine
# One-step PostgreSQL bootstrap script
#
# Installs PostgreSQL + pgvector (local only), creates the application user,
# database, extension, and grants all required permissions.
#
# Supports both local and remote PostgreSQL servers.
# For remote servers, set DB_HOST to the server address — local installation
# steps are automatically skipped.
#
# Usage:
#   chmod +x bootstrap_db.sh
#   sudo ./bootstrap_db.sh                          # Local setup
#   DB_HOST=db.example.com ./bootstrap_db.sh        # Remote setup
#
# Defaults match global_config.yaml. Override with environment variables:
#   DB_USER, DB_PASSWORD, DB_NAME, DB_HOST, DB_PORT,
#   DB_ADMIN_USER, DB_ADMIN_PASSWORD,
#   DB_SSL_MODE, DB_SSL_CA, DB_SSL_CERT, DB_SSL_KEY
# ============================================================================

set -euo pipefail

# ---------- Configurable defaults (match global_config.yaml) ----------
DB_USER="${DB_USER:-codebase_analytics_user}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-codebase_analytics_db}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# Admin credentials (for remote servers)
DB_ADMIN_USER="${DB_ADMIN_USER:-postgres}"
DB_ADMIN_PASSWORD="${DB_ADMIN_PASSWORD:-}"

# SSL / TLS configuration
DB_SSL_MODE="${DB_SSL_MODE:-prefer}"     # disable | allow | prefer | require | verify-ca | verify-full
DB_SSL_CA="${DB_SSL_CA:-}"               # Path to CA certificate
DB_SSL_CERT="${DB_SSL_CERT:-}"           # Path to client certificate
DB_SSL_KEY="${DB_SSL_KEY:-}"             # Path to client private key

# ---------- Colors for output ----------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------- Detect OS ----------
OS_TYPE="linux"
if [[ "$(uname -s)" == "Darwin" ]]; then
    OS_TYPE="macos"
fi

# ---------- Detect the real (non-root) user ----------
REAL_USER="${SUDO_USER:-$(whoami)}"

# ---------- Detect if host is remote ----------
IS_REMOTE=false
if [[ "$DB_HOST" != "localhost" && "$DB_HOST" != "127.0.0.1" && "$DB_HOST" != "::1" ]]; then
    IS_REMOTE=true
fi

# ---------- Build PSQL SSL options ----------
PSQL_SSL_OPTS=""
if [[ "$DB_SSL_MODE" != "disable" && -n "$DB_SSL_MODE" ]]; then
    PSQL_SSL_OPTS="sslmode=${DB_SSL_MODE}"
fi
if [[ -n "$DB_SSL_CA" ]]; then
    PSQL_SSL_OPTS="${PSQL_SSL_OPTS:+$PSQL_SSL_OPTS }'sslrootcert=${DB_SSL_CA}'"
fi
if [[ -n "$DB_SSL_CERT" ]]; then
    PSQL_SSL_OPTS="${PSQL_SSL_OPTS:+$PSQL_SSL_OPTS }'sslcert=${DB_SSL_CERT}'"
fi
if [[ -n "$DB_SSL_KEY" ]]; then
    PSQL_SSL_OPTS="${PSQL_SSL_OPTS:+$PSQL_SSL_OPTS }'sslkey=${DB_SSL_KEY}'"
fi

# ---------- Pre-flight connectivity check ----------
info "Testing connectivity to ${DB_HOST}:${DB_PORT}..."
if command -v pg_isready &>/dev/null; then
    if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -t 10 &>/dev/null; then
        if [[ "$IS_REMOTE" == "true" ]]; then
            err "Cannot reach PostgreSQL at ${DB_HOST}:${DB_PORT}."
            err "Check: host/port, firewall rules, pg_hba.conf on server."
            if [[ "$DB_SSL_MODE" == "require" || "$DB_SSL_MODE" == "verify-ca" || "$DB_SSL_MODE" == "verify-full" ]]; then
                err "SSL mode is '${DB_SSL_MODE}' — ensure the server supports SSL."
            fi
            exit 1
        else
            info "PostgreSQL not yet running locally. Will attempt to start after installation."
        fi
    else
        info "PostgreSQL reachable at ${DB_HOST}:${DB_PORT}."
    fi
else
    warn "pg_isready not found — skipping pre-flight check."
fi

# ---------- Step 1: Install PostgreSQL + pgvector (local only) ----------
if [[ "$IS_REMOTE" == "true" ]]; then
    info "Remote host detected (${DB_HOST}). Skipping local PostgreSQL installation."
else
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
fi

# ---------- Step 2: Create user, database, extension, and permissions ----------
info "Setting up database '${DB_NAME}' with user '${DB_USER}'..."

# Build the psql command based on local vs remote
if [[ "$IS_REMOTE" == "true" ]]; then
    # Remote: connect using admin credentials over network
    export PGPASSWORD="${DB_ADMIN_PASSWORD}"
    PSQL_CMD="psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_ADMIN_USER} -d postgres"
    info "Connecting to remote server as '${DB_ADMIN_USER}'..."
elif [[ "$OS_TYPE" == "macos" ]]; then
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
if [[ "$IS_REMOTE" == "true" ]]; then
    VEC_PSQL="psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_ADMIN_USER} -d ${DB_NAME}"
elif [[ "$OS_TYPE" == "macos" ]]; then
    VEC_PSQL="psql -U ${REAL_USER} -d ${DB_NAME}"
else
    VEC_PSQL="sudo -u postgres psql -d ${DB_NAME}"
fi
$VEC_PSQL -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null && \
    info "pgvector extension enabled." || \
    warn "pgvector extension not available — vector DB features will be unavailable. Core features (telemetry, analysis, fixer) are unaffected."

# Step 2c: Grants and default privileges (must always run)
if [[ "$IS_REMOTE" == "true" ]]; then
    GRANT_PSQL="psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_ADMIN_USER} -d ${DB_NAME}"
elif [[ "$OS_TYPE" == "macos" ]]; then
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

# Clean up remote password
if [[ "$IS_REMOTE" == "true" ]]; then
    unset PGPASSWORD
fi

# ---------- Summary ----------
info "Bootstrap complete!"
info "  Database : ${DB_NAME}"
info "  User     : ${DB_USER}"
info "  Host     : ${DB_HOST}:${DB_PORT}"
info "  SSL Mode : ${DB_SSL_MODE}"
if [[ "$IS_REMOTE" == "true" ]]; then
    info "  Mode     : Remote server"
else
    info "  Mode     : Local"
fi
info ""

# Build connection string with SSL
CONN_STR="postgresql+psycopg2://${DB_USER}:****@${DB_HOST}:${DB_PORT}/${DB_NAME}"
if [[ "$DB_SSL_MODE" != "disable" && -n "$DB_SSL_MODE" ]]; then
    CONN_STR="${CONN_STR}?sslmode=${DB_SSL_MODE}"
fi
info "Connection string: ${CONN_STR}"

# SSL guidance for secure modes
if [[ "$DB_SSL_MODE" == "require" || "$DB_SSL_MODE" == "verify-ca" || "$DB_SSL_MODE" == "verify-full" ]]; then
    info ""
    info "SSL Configuration Notes:"
    info "  - Ensure the PostgreSQL server has ssl=on in postgresql.conf"
    info "  - Server certificate: ssl_cert_file, ssl_key_file in postgresql.conf"
    if [[ "$DB_SSL_MODE" == "verify-ca" || "$DB_SSL_MODE" == "verify-full" ]]; then
        info "  - Client must have the CA certificate (set DB_SSL_CA)"
        info "  - For verify-full: server CN must match DB_HOST"
    fi
fi
