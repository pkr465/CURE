-- CURE — Codebase Update & Refactor Engine
-- PostgreSQL Schema Setup for Vector DB
-- Run with:
--   psql -U postgres -d postgres -a -e -f db/schema_codebase_analytics.sql

------------------------------------------------------------
-- 1. Create application database if it does not exist
------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_database
        WHERE datname = 'codebase_analytics_db'
    ) THEN
        CREATE DATABASE codebase_analytics_db
            OWNER codebase_analytics_user;
    END IF;
END
$$;

------------------------------------------------------------
-- 2. Connect to the application database
------------------------------------------------------------

\c codebase_analytics_db

------------------------------------------------------------
-- 3. Enable pgvector extension (for embeddings)
------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;

------------------------------------------------------------
-- 4. Create collection table (if not exists)
--    Stores logical collections of documents/embeddings
------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_collection'
          AND table_schema = 'public'
    ) THEN
        CREATE TABLE public.langchain_pg_collection (
            uuid      UUID PRIMARY KEY,
            name      TEXT,
            cmetadata JSONB
        );
    END IF;
END
$$;

------------------------------------------------------------
-- 5. Create embedding table (if not exists)
--    Stores embeddings + metadata for each document/chunk
------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_embedding'
          AND table_schema = 'public'
    ) THEN
        CREATE TABLE public.langchain_pg_embedding (
            id            UUID PRIMARY KEY,
            collection_id UUID NOT NULL
                           REFERENCES public.langchain_pg_collection(uuid)
                           ON DELETE CASCADE,
            embedding     VECTOR(1024),
            document      TEXT,
            cmetadata     JSONB,
            source_file   TEXT,
            ingested_at   TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END
$$;

------------------------------------------------------------
-- 6. (Optional) Clean out existing data
--    Safe to comment out in production if you don't want truncation
------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_embedding'
          AND table_schema = 'public'
    ) AND EXISTS (
        SELECT 1
        FROM information_schema.tables 
        WHERE table_name = 'langchain_pg_collection'
          AND table_schema = 'public'
    ) THEN
        TRUNCATE TABLE public.langchain_pg_embedding,
                       public.langchain_pg_collection
        RESTART IDENTITY;
    END IF;
END
$$;

------------------------------------------------------------
-- 7. Quick sanity checks (optional; mostly for dev)
------------------------------------------------------------

-- List extensions (should include "vector")
\dx

-- Show table schemas
\d+ public.langchain_pg_collection
\d+ public.langchain_pg_embedding

-- Count existing embeddings
SELECT COUNT(*) AS embedding_count
FROM public.langchain_pg_embedding;

------------------------------------------------------------
-- 8. Optional: Example uniqueness constraint on JSONB field
--    Ensures cmetadata->>'uuid' is unique across embeddings
------------------------------------------------------------

-- ALTER TABLE public.langchain_pg_embedding
-- ADD CONSTRAINT unique_uuid_in_metadata
--     UNIQUE ((cmetadata->>'uuid'));

------------------------------------------------------------
-- 9. Sample query: fetch embeddings by metadata UUIDs
------------------------------------------------------------

SELECT
    cmetadata->>'uuid' AS uuid
FROM public.langchain_pg_embedding
WHERE cmetadata->>'uuid' IN (
    'bd52117fa6a25b2e19',
    'be15d7264ab7e25de5797cf38828c252',
    'c9b7c9b79579073ad65a2d3af938164d',
    '640aeb557bc1d37d92ce8d7791d8050d'
);