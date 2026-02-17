-- CURE — Codebase Update & Refactor Engine
-- Quick PGVector setup runner

psql -U postgres -d postgres -f db/pgvector_roles_and_extensions.sql
psql -U postgres -d postgres -c \
"CREATE DATABASE codebase_analytics_db OWNER codebase_analytics_user;"
psql -U postgres -d codebase_analytics_db -f db/schema_codebase_analytics.sql