# ============================================================================
# CURE — Codebase Update & Refactor Engine
# One-step PostgreSQL bootstrap script (Windows / PowerShell)
#
# Installs PostgreSQL + pgvector, creates the application user, database,
# extension, and grants all required permissions.
#
# Usage (run as Administrator):
#   .\bootstrap_db.ps1
#
# Defaults match global_config.yaml. Override with environment variables:
#   $env:DB_USER, $env:DB_PASSWORD, $env:DB_NAME, $env:DB_HOST, $env:DB_PORT
# ============================================================================

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

# ---------- Configurable defaults (match global_config.yaml) ----------
$DB_USER     = if ($env:DB_USER)     { $env:DB_USER }     else { "codebase_analytics_user" }
$DB_PASSWORD = if ($env:DB_PASSWORD) { $env:DB_PASSWORD } else { "postgres" }
$DB_NAME     = if ($env:DB_NAME)     { $env:DB_NAME }     else { "codebase_analytics_db" }
$DB_HOST     = if ($env:DB_HOST)     { $env:DB_HOST }     else { "localhost" }
$DB_PORT     = if ($env:DB_PORT)     { $env:DB_PORT }     else { "5432" }

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

# ---------- Step 1: Install PostgreSQL ----------
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

Info "Using psql: $psqlPath"

# Add psql directory to PATH for this session
$psqlDir = Split-Path $psqlPath -Parent
if ($env:Path -notlike "*$psqlDir*") {
    $env:Path = "$psqlDir;$env:Path"
}

# ---------- Step 1b: Install pgvector extension ----------
Info "Checking for pgvector extension..."

# pgvector on Windows typically needs to be built from source or installed manually.
# Try to detect it; if missing, provide guidance.
$pgDir = (Split-Path $psqlDir -Parent)  # e.g. C:\Program Files\PostgreSQL\16
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
    Warn "After installing pgvector, re-run this script to complete setup."
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

# ---------- Step 3: Create user, database, extension, and permissions ----------
Info "Setting up database '$DB_NAME' with user '$DB_USER'..."

# Build the SQL script
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
$env:PGPASSWORD = "postgres"
$sqlScript | & $psqlPath -U postgres -h $DB_HOST -p $DB_PORT -d postgres -v ON_ERROR_STOP=1

# Create database if it doesn't exist
$dbExists = & $psqlPath -U postgres -h $DB_HOST -p $DB_PORT -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'"
if ($dbExists.Trim() -ne "1") {
    Info "Creating database '$DB_NAME'..."
    & $psqlPath -U postgres -h $DB_HOST -p $DB_PORT -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
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

$configSql | & $psqlPath -U postgres -h $DB_HOST -p $DB_PORT -d $DB_NAME -v ON_ERROR_STOP=1

# Clean up
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

# ---------- Done ----------
Info "Bootstrap complete!"
Info "  Database : $DB_NAME"
Info "  User     : $DB_USER"
Info "  Host     : ${DB_HOST}:${DB_PORT}"
Info ""
Info "Connection string: postgresql+psycopg2://${DB_USER}:****@${DB_HOST}:${DB_PORT}/${DB_NAME}"
