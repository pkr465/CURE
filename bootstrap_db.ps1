# ============================================================================
# CURE — Codebase Update & Refactor Engine
# One-step PostgreSQL bootstrap script (Windows / PowerShell)
#
# Installs PostgreSQL + pgvector (local only), creates the application user,
# database, extension, and grants all required permissions.
#
# Supports both local and remote PostgreSQL servers.
# For remote servers, set DB_HOST to the server address — local installation
# steps are automatically skipped.
#
# Usage (run as Administrator for local setup):
#   .\bootstrap_db.ps1                                    # Local setup
#   $env:DB_HOST="db.example.com"; .\bootstrap_db.ps1    # Remote setup
#
# Defaults match global_config.yaml. Override with environment variables:
#   $env:DB_USER, $env:DB_PASSWORD, $env:DB_NAME, $env:DB_HOST, $env:DB_PORT,
#   $env:DB_ADMIN_USER, $env:DB_ADMIN_PASSWORD,
#   $env:DB_SSL_MODE, $env:DB_SSL_CA
# ============================================================================

$ErrorActionPreference = "Stop"

# ---------- Configurable defaults (match global_config.yaml) ----------
$DB_USER           = if ($env:DB_USER)           { $env:DB_USER }           else { "codebase_analytics_user" }
$DB_PASSWORD       = if ($env:DB_PASSWORD)       { $env:DB_PASSWORD }       else { "postgres" }
$DB_NAME           = if ($env:DB_NAME)           { $env:DB_NAME }           else { "codebase_analytics_db" }
$DB_HOST           = if ($env:DB_HOST)           { $env:DB_HOST }           else { "localhost" }
$DB_PORT           = if ($env:DB_PORT)           { $env:DB_PORT }           else { "5432" }
$DB_ADMIN_USER     = if ($env:DB_ADMIN_USER)     { $env:DB_ADMIN_USER }     else { "postgres" }
$DB_ADMIN_PASSWORD = if ($env:DB_ADMIN_PASSWORD) { $env:DB_ADMIN_PASSWORD } else { "postgres" }
$DB_SSL_MODE       = if ($env:DB_SSL_MODE)       { $env:DB_SSL_MODE }       else { "prefer" }
$DB_SSL_CA         = if ($env:DB_SSL_CA)         { $env:DB_SSL_CA }         else { "" }

# ---------- Detect remote vs local ----------
$IsRemote = ($DB_HOST -ne "localhost" -and $DB_HOST -ne "127.0.0.1" -and $DB_HOST -ne "::1")

# ---------- Helpers ----------
function Info  { param([string]$msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Warn  { param([string]$msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Fatal { param([string]$msg) Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

# ---------- Locate psql or install PostgreSQL ----------
function Find-Psql {
    # Check PATH first
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql) { return $psql.Source }

    # Common PostgreSQL install locations on Windows
    $searchPaths = @(
        "C:\Program Files\PostgreSQL\*\bin\psql.exe",
        "C:\Program Files (x86)\PostgreSQL\*\bin\psql.exe"
    )
    foreach ($pattern in $searchPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
                 Sort-Object { [int]($_.Directory.Parent.Name) } -Descending |
                 Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

# ---------- Pre-flight connectivity check ----------
Info "Testing connectivity to ${DB_HOST}:${DB_PORT}..."
$pgIsReady = Get-Command pg_isready -ErrorAction SilentlyContinue
if ($pgIsReady) {
    $result = & pg_isready -h $DB_HOST -p $DB_PORT -t 10 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($IsRemote) {
            Fatal "Cannot reach PostgreSQL at ${DB_HOST}:${DB_PORT}. Check host/port/firewall."
        } else {
            Info "PostgreSQL not yet running locally. Will attempt to start after installation."
        }
    } else {
        Info "PostgreSQL reachable at ${DB_HOST}:${DB_PORT}."
    }
} else {
    Warn "pg_isready not found - skipping pre-flight check."
}

# ---------- Step 1: Install PostgreSQL (local only) ----------
if ($IsRemote) {
    Info "Remote host detected ($DB_HOST). Skipping local PostgreSQL installation."
    $psqlPath = Find-Psql
    if (-not $psqlPath) {
        Fatal "psql client not found. Install PostgreSQL client tools to connect to remote server."
    }
} else {
    Info "Checking for PostgreSQL installation..."

    $psqlPath = Find-Psql

    if (-not $psqlPath) {
        Info "PostgreSQL not found. Attempting to install..."

        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Info "Installing PostgreSQL 16 via winget..."
            winget install --id PostgreSQL.PostgreSQL.16 --accept-source-agreements --accept-package-agreements --silent
        }
        elseif (Get-Command choco -ErrorAction SilentlyContinue) {
            Info "Installing PostgreSQL 16 via Chocolatey..."
            choco install postgresql16 --yes --params "/Password:postgres"
        }
        else {
            Fatal "Neither winget nor Chocolatey found. Please install PostgreSQL manually from https://www.postgresql.org/download/windows/"
        }

        # Refresh PATH after install
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")

        $psqlPath = Find-Psql
        if (-not $psqlPath) {
            Fatal "PostgreSQL installed but psql not found on PATH. Please add the PostgreSQL bin directory to PATH and re-run."
        }
    }

    # ---------- Step 1b: Install pgvector extension ----------
    Info "Checking for pgvector extension..."

    $psqlDir = Split-Path $psqlPath -Parent
    $pgDir = (Split-Path $psqlDir -Parent)
    $pgvectorLib = Join-Path $pgDir "lib\vector.dll"
    $pgvectorSql = Join-Path $pgDir "share\extension\vector--*.sql"

    if (-not (Test-Path $pgvectorLib) -and -not (Get-ChildItem $pgvectorSql -ErrorAction SilentlyContinue)) {
        Warn "pgvector extension not detected in $pgDir"
        Warn "pgvector must be installed separately on Windows."
        Warn "Options:"
        Warn "  1. Download prebuilt binaries: https://github.com/pgvector/pgvector/releases"
        Warn "  2. Build from source: https://github.com/pgvector/pgvector#windows"
        Warn "  3. Use a Docker container with pgvector pre-installed"
        Warn ""
        Warn "Continuing with database setup (CREATE EXTENSION may fail)..."
    }
    else {
        Info "pgvector extension found."
    }

    # ---------- Step 2: Ensure PostgreSQL service is running ----------
    Info "Ensuring PostgreSQL service is running..."

    $pgService = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pgService) {
        if ($pgService.Status -ne "Running") {
            Start-Service $pgService.Name
            Info "Started service: $($pgService.Name)"
        }
        else {
            Info "Service already running: $($pgService.Name)"
        }
    }
    else {
        Warn "No PostgreSQL Windows service found. Ensure PostgreSQL is running before proceeding."
    }
}

Info "Using psql: $psqlPath"

# Add psql directory to PATH for this session
$psqlDir = Split-Path $psqlPath -Parent
if ($env:Path -notlike "*$psqlDir*") {
    $env:Path = "$psqlDir;$env:Path"
}

# ---------- Step 3: Create user, database, extension, and permissions ----------
Info "Setting up database '$DB_NAME' with user '$DB_USER'..."

# Set admin password for psql
if ($IsRemote) {
    $env:PGPASSWORD = $DB_ADMIN_PASSWORD
    $PSQL_USER = $DB_ADMIN_USER
    Info "Connecting to remote server as '$DB_ADMIN_USER'..."
} else {
    $env:PGPASSWORD = "postgres"
    $PSQL_USER = "postgres"
}

# Build the SQL script for user creation
$sqlScript = @"
-- Create application user (skip if exists)
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
        CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
    END IF;
END
`$`$;
"@

# Run user creation against the default 'postgres' database
$sqlScript | & $psqlPath -U $PSQL_USER -h $DB_HOST -p $DB_PORT -d postgres -v ON_ERROR_STOP=1

# Create database if it doesn't exist
$dbExists = & $psqlPath -U $PSQL_USER -h $DB_HOST -p $DB_PORT -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'"
if ($dbExists.Trim() -ne "1") {
    Info "Creating database '$DB_NAME'..."
    & $psqlPath -U $PSQL_USER -h $DB_HOST -p $DB_PORT -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
}
else {
    Info "Database '$DB_NAME' already exists."
}

# Configure extensions and permissions on the target database
$configSql = @"
CREATE EXTENSION IF NOT EXISTS vector;

GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
GRANT USAGE ON SCHEMA public TO $DB_USER;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $DB_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $DB_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO $DB_USER;
"@

$configSql | & $psqlPath -U $PSQL_USER -h $DB_HOST -p $DB_PORT -d $DB_NAME -v ON_ERROR_STOP=1

# Clean up
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

# ---------- Summary ----------
Info "Bootstrap complete!"
Info "  Database : $DB_NAME"
Info "  User     : $DB_USER"
Info "  Host     : ${DB_HOST}:${DB_PORT}"
Info "  SSL Mode : $DB_SSL_MODE"
if ($IsRemote) {
    Info "  Mode     : Remote server"
} else {
    Info "  Mode     : Local"
}
Info ""

$connStr = "postgresql+psycopg2://${DB_USER}:****@${DB_HOST}:${DB_PORT}/${DB_NAME}"
if ($DB_SSL_MODE -ne "disable" -and $DB_SSL_MODE -ne "") {
    $connStr += "?sslmode=$DB_SSL_MODE"
}
Info "Connection string: $connStr"

if ($DB_SSL_MODE -eq "require" -or $DB_SSL_MODE -eq "verify-ca" -or $DB_SSL_MODE -eq "verify-full") {
    Info ""
    Info "SSL Configuration Notes:"
    Info "  - Ensure the PostgreSQL server has ssl=on in postgresql.conf"
    Info "  - Server certificate: ssl_cert_file, ssl_key_file in postgresql.conf"
    if ($DB_SSL_MODE -eq "verify-ca" -or $DB_SSL_MODE -eq "verify-full") {
        Info "  - Client must have the CA certificate (set DB_SSL_CA)"
        Info "  - For verify-full: server CN must match DB_HOST"
    }
}
