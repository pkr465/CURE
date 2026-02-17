#!/bin/bash
# CURE — Codebase Update & Refactor Engine
# PostgreSQL / PGVector initialisation script
# Note: environment-specific values may need adjustment per deployment

# SQLAlchemy-style connection string for the *app user* (used by your app)
POSTGRES_CONNECTION=postgresql+psycopg2://codebase_analytics_user:postgres@localhost/codebase_analytics_db

# Admin credentials (used only for migrations/setup scripts, if needed)
# Often this is just the default 'postgres' superuser in local dev.
POSTGRES_ADMIN_USERNAME=postgres
POSTGRES_ADMIN_PASSWORD=postgres

# LangChain / pgvector collection configuration
POSTGRES_COLLECTION=codebase_analytics_data_2025
POSTGRES_COLLECTION_TABLENAME=langchain_pg_collection
POSTGRES_EMBEDDING_TABLENAME=langchain_pg_embedding
POSTGRES_STORE_NAME=codebase_analytics_vector_db

# Application DB user (same as in POSTGRES_CONNECTION)
POSTGRES_USERNAME=codebase_analytics_user
POSTGRES_PASSWORD=postgres

# Database name and connection host/port
POSTGRES_DATABASE=codebase_analytics_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Variables
# Variables

# Application database name (matches schema_codebase_analytics.sql)
DB_NAME="codebase_analytics_db"

# Application DB user (non-superuser; used by your app)
DB_USER="codebase_analytics_user"

# Admin/superuser for setup/migrations (often just 'postgres' locally)
DB_SUPERUSER="postgres"

# Password for the application DB user (set this to the same value you used in role creation)
DB_PASSWORD="postgres"

# Create postgres user if it doesn't exist
psql -U "$DB_SUPERUSER" -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
psql -U "$DB_SUPERUSER" -c "CREATE ROLE $DB_USER WITH LOGIN SUPERUSER PASSWORD '$DB_PASSWORD';"

# Create codebase_analytics_db database if it doesn't exist
psql -U "$DB_SUPERUSER" -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
psql -U "$DB_SUPERUSER" -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# Run schema setup inside codebase_analytics_db
psql -U "$DB_SUPERUSER" -d "$DB_NAME" <<'EOF'

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create langchain_pg_collection table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_collection'
    ) THEN
        CREATE TABLE langchain_pg_collection (
            uuid UUID PRIMARY KEY,
            name TEXT,
            cmetadata JSONB
        );
    END IF;
END
$$;

-- Create langchain_pg_embedding table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_embedding'
    ) THEN
        CREATE TABLE langchain_pg_embedding (
            id UUID PRIMARY KEY,
            collection_id UUID NOT NULL REFERENCES langchain_pg_collection(uuid) ON DELETE CASCADE,
            embedding VECTOR(1024),
            document TEXT,
            cmetadata JSONB,
            source_file TEXT,
            ingested_at TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END
$$;

-- Clean tables if they exist
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_embedding'
    ) THEN
        TRUNCATE TABLE langchain_pg_embedding RESTART IDENTITY;
    END IF;

    IF EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_collection'
    ) THEN
        TRUNCATE TABLE langchain_pg_collection RESTART IDENTITY;
    END IF;
END
$$;

EOF

echo "✅ Setup complete for database '$DB_NAME' with user '$DB_USER'"
