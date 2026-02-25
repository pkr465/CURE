"""
PostgreSQL Database Setup Utility

CURE — Codebase Update & Refactor Engine

Safe and Idempotent PostgreSQL Database and Schema Setup Utility:
- Ensures user/role exists (creates if missing)
- Ensures database exists (creates if missing) and owned properly
- Enables pgvector
- Creates required tables for vector/document storage (if missing)
- Never drops or overwrites existing tables/data
"""

import logging
from typing import Optional, Union
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from psycopg2 import sql

# Configure logging
logger = logging.getLogger(__name__)

# Import GlobalConfig with fallback to EnvConfig
try:
    from utils.parsers.global_config_parser import GlobalConfig
except ImportError:
    GlobalConfig = None
from utils.parsers.env_parser import EnvConfig


def create_role_if_not_exists(
    conn: psycopg2.extensions.connection,
    target_user: str,
    target_password: str,
    createdb: bool = False
) -> None:
    """
    Checks and creates a PostgreSQL role if it does not exist.

    Args:
        conn: PostgreSQL connection object.
        target_user: The username/role name to create.
        target_password: The password for the role.
        createdb: Whether to grant CREATEDB privilege.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (target_user,))
        if not cur.fetchone():
            logger.info(f"Creating role/user '{target_user}' ...")
            query = sql.SQL(
                "CREATE USER {role} WITH PASSWORD %s {createdb}"
            ).format(
                role=sql.Identifier(target_user),
                createdb=sql.SQL("CREATEDB") if createdb else sql.SQL("")
            )
            cur.execute(query, [target_password])
            logger.info(f"Role/user '{target_user}' created.")
        else:
            logger.debug(f"Role/user '{target_user}' already exists.")


class PostgresDbSetup:
    """
    Safe and Idempotent PostgreSQL Database and Schema Setup Utility

    Supports configuration via GlobalConfig or EnvConfig with automatic fallback.
    """

    def __init__(
        self,
        environment: Optional[Union["GlobalConfig", EnvConfig]] = None
    ) -> None:
        """
        Initialize PostgreSQL database setup with configuration.

        Args:
            environment: Configuration object (GlobalConfig, EnvConfig, or None).
                        If None, attempts GlobalConfig first, then falls back to EnvConfig.
        """
        # Load or validate environment with GlobalConfig fallback
        if environment is not None:
            self.env = environment
        else:
            # Try GlobalConfig first, fall back to EnvConfig
            if GlobalConfig is not None:
                try:
                    self.env = GlobalConfig()
                    logger.debug("Using GlobalConfig for environment configuration")
                except Exception as e:
                    logger.debug(
                        f"GlobalConfig initialization failed ({e}), falling back to EnvConfig"
                    )
                    self.env = EnvConfig()
            else:
                self.env = EnvConfig()
                logger.debug("Using EnvConfig for environment configuration")

        # Try to get 'admin'/superuser credentials for setup
        self.admin_user: str = self.env.get('POSTGRES_ADMIN_USERNAME', 'codebase_analytics_pg')
        self.admin_password: str = self.env.get('POSTGRES_ADMIN_PASSWORD', 'codebase_analytics_pg')

        self.username: str = self.env.get('POSTGRES_USERNAME')
        self.password: str = self.env.get('POSTGRES_PASSWORD')
        self.database: str = self.env.get('POSTGRES_DATABASE')
        self.host: str = self.env.get('POSTGRES_HOST', 'localhost')
        self.port: int = self.env.get('POSTGRES_PORT', 5432)
        self.collection_table: str = self.env.get('POSTGRES_COLLECTION_TABLENAME')
        self.embedding_table: str = self.env.get('POSTGRES_EMBEDDING_TABLENAME')

        # SSL / remote server support
        self.ssl_mode: str = self.env.get('POSTGRES_SSL_MODE', 'prefer')
        self.ssl_ca: str = self.env.get('POSTGRES_SSL_CA', '')
        self.ssl_cert: str = self.env.get('POSTGRES_SSL_CERT', '')
        self.ssl_key: str = self.env.get('POSTGRES_SSL_KEY', '')

    def _get_ssl_kwargs(self) -> dict:
        """Build SSL-related kwargs for psycopg2 connections."""
        kwargs = {}
        if self.ssl_mode and self.ssl_mode != 'disable':
            kwargs['sslmode'] = self.ssl_mode
        if self.ssl_ca:
            kwargs['sslrootcert'] = self.ssl_ca
        if self.ssl_cert:
            kwargs['sslcert'] = self.ssl_cert
        if self.ssl_key:
            kwargs['sslkey'] = self.ssl_key
        return kwargs

    def is_remote(self) -> bool:
        """Check if the database host is a remote server."""
        return self.host not in ('localhost', '127.0.0.1', '::1', '')

    def pre_flight_check(self) -> tuple:
        """
        Attempt a lightweight connection to verify DB reachability.
        Returns (success: bool, error_message: str).
        """
        try:
            ssl_kwargs = self._get_ssl_kwargs()
            conn = psycopg2.connect(
                dbname='postgres',
                user=self.admin_user,
                password=self.admin_password,
                host=self.host,
                port=self.port,
                connect_timeout=10,
                **ssl_kwargs,
            )
            conn.close()
            logger.info(
                f"Pre-flight check passed: {self.host}:{self.port} "
                f"(ssl={self.ssl_mode})"
            )
            return (True, "")
        except Exception as ex:
            msg = f"Pre-flight check failed for {self.host}:{self.port}: {ex}"
            logger.error(msg)
            return (False, msg)

    def create_db_if_not_exists(self, conn: psycopg2.extensions.connection) -> None:
        """
        Creates the PostgreSQL database if it does not already exist.

        Args:
            conn: PostgreSQL connection object (must be connected to 'postgres' DB).
        """
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (self.database,))
            if not cur.fetchone():
                logger.info(f"Creating database '{self.database}' with owner '{self.username}' ...")
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {};").format(
                        sql.Identifier(self.database),
                        sql.Identifier(self.username)
                    )
                )
                logger.info(f"Database '{self.database}' created.")
            else:
                logger.debug(f"Database '{self.database}' already exists.")

    def run_schema_setup(self, dbconn: psycopg2.extensions.connection) -> None:
        """
        Sets up the database schema: enables pgvector and creates tables.

        Args:
            dbconn: PostgreSQL connection object (connected to the target database).
        """
        # Enable pgvector extension (must be superuser)
        with dbconn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                logger.info("pgvector extension enabled.")
            except psycopg2.errors.InsufficientPrivilege:
                logger.warning(
                    "Not superuser, skipping CREATE EXTENSION vector. "
                    "Please ensure it's installed by a superuser."
                )
                dbconn.rollback()
            except Exception as ex:
                logger.error(f"Could not create extension 'vector': {ex}")
                dbconn.rollback()

        # Create tables if not exist
        with dbconn.cursor() as cur:
            logger.info(f"Creating table {self.collection_table} (if not exists)...")
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    uuid UUID PRIMARY KEY,
                    name TEXT,
                    cmetadata JSONB
                );
            """).format(sql.Identifier(self.collection_table)))

            logger.info(f"Creating table {self.embedding_table} (if not exists)...")
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id UUID PRIMARY KEY,
                    collection_id UUID NOT NULL REFERENCES {}(uuid) ON DELETE CASCADE,
                    embedding VECTOR(1024),
                    document TEXT,
                    cmetadata JSONB,
                    source_file TEXT,
                    ingested_at TIMESTAMPTZ DEFAULT NOW()
                );
            """).format(
                sql.Identifier(self.embedding_table),
                sql.Identifier(self.collection_table)
            ))

            dbconn.commit()
            logger.info("Vector schema/tables ready.")

        # Create telemetry & HITL tables
        self._setup_telemetry_and_hitl_tables(dbconn)

    def _setup_telemetry_and_hitl_tables(
        self, dbconn: psycopg2.extensions.connection
    ) -> None:
        """Create telemetry and HITL tables if they don't exist."""
        with dbconn.cursor() as cur:
            # ── Telemetry runs ────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_runs (
                    run_id              TEXT        PRIMARY KEY,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at         TIMESTAMPTZ,
                    mode                TEXT        NOT NULL,
                    status              TEXT        NOT NULL DEFAULT 'started',
                    codebase_path       TEXT,
                    files_analyzed      INTEGER     DEFAULT 0,
                    total_chunks        INTEGER     DEFAULT 0,
                    issues_total        INTEGER     DEFAULT 0,
                    issues_critical     INTEGER     DEFAULT 0,
                    issues_high         INTEGER     DEFAULT 0,
                    issues_medium       INTEGER     DEFAULT 0,
                    issues_low          INTEGER     DEFAULT 0,
                    issues_fixed        INTEGER     DEFAULT 0,
                    issues_skipped      INTEGER     DEFAULT 0,
                    issues_failed       INTEGER     DEFAULT 0,
                    llm_provider        TEXT,
                    llm_model           TEXT,
                    total_llm_calls     INTEGER     DEFAULT 0,
                    total_prompt_tokens  INTEGER    DEFAULT 0,
                    total_completion_tokens INTEGER DEFAULT 0,
                    total_llm_latency_ms INTEGER   DEFAULT 0,
                    use_ccls            BOOLEAN     DEFAULT FALSE,
                    use_hitl            BOOLEAN     DEFAULT FALSE,
                    constraints_used    TEXT,
                    duration_seconds    REAL,
                    metadata            JSONB
                )
            """)

            # ── Telemetry events ──────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    event_id            BIGSERIAL   PRIMARY KEY,
                    run_id              TEXT        NOT NULL
                                         REFERENCES telemetry_runs(run_id)
                                         ON DELETE CASCADE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    event_type          TEXT        NOT NULL,
                    file_path           TEXT,
                    line_number         INTEGER,
                    issue_type          TEXT,
                    severity            TEXT,
                    llm_provider        TEXT,
                    llm_model           TEXT,
                    prompt_tokens       INTEGER,
                    completion_tokens   INTEGER,
                    latency_ms          INTEGER,
                    detail              JSONB
                )
            """)

            # ── HITL feedback decisions ───────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hitl_feedback_decisions (
                    id                  TEXT        PRIMARY KEY,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source              TEXT        NOT NULL,
                    file_path           TEXT        NOT NULL,
                    line_number         INTEGER,
                    code_snippet        TEXT,
                    issue_type          TEXT,
                    severity            TEXT,
                    human_action        TEXT        NOT NULL,
                    human_feedback_text TEXT,
                    applied_constraints JSONB,
                    remediation_notes   TEXT,
                    agent_that_flagged  TEXT,
                    run_id              TEXT
                )
            """)

            # ── HITL constraint rules ─────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hitl_constraint_rules (
                    rule_id               TEXT  PRIMARY KEY,
                    description           TEXT,
                    standard_remediation  TEXT,
                    llm_action            TEXT,
                    reasoning             TEXT,
                    example_allowed       TEXT,
                    example_prohibited    TEXT,
                    applies_to_patterns   JSONB,
                    source_file           TEXT
                )
            """)

            # ── HITL run metadata ─────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hitl_run_metadata (
                    run_id           TEXT        PRIMARY KEY,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    config_snapshot  JSONB
                )
            """)

            # ── Telemetry findings (per-issue detail) ──────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_findings (
                    finding_id          BIGSERIAL   PRIMARY KEY,
                    run_id              TEXT        NOT NULL
                                         REFERENCES telemetry_runs(run_id)
                                         ON DELETE CASCADE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    file_path           TEXT,
                    line_start          INTEGER,
                    line_end            INTEGER,
                    title               TEXT,
                    category            TEXT,
                    severity            TEXT,
                    confidence          TEXT,
                    description         TEXT,
                    suggestion          TEXT,
                    code_snippet        TEXT,
                    fixed_code          TEXT,
                    is_false_positive   BOOLEAN     DEFAULT FALSE,
                    user_feedback       TEXT,
                    metadata            JSONB
                )
            """)

            # ── Telemetry LLM calls (per-invocation) ──────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_llm_calls (
                    call_id             BIGSERIAL   PRIMARY KEY,
                    run_id              TEXT        NOT NULL
                                         REFERENCES telemetry_runs(run_id)
                                         ON DELETE CASCADE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    provider            TEXT,
                    model               TEXT,
                    purpose             TEXT,
                    file_path           TEXT,
                    chunk_index         INTEGER,
                    prompt_tokens       INTEGER     DEFAULT 0,
                    completion_tokens   INTEGER     DEFAULT 0,
                    total_tokens        INTEGER     DEFAULT 0,
                    latency_ms          INTEGER     DEFAULT 0,
                    estimated_cost_usd  NUMERIC(10,6) DEFAULT 0,
                    status              TEXT        DEFAULT 'success',
                    error_message       TEXT,
                    metadata            JSONB
                )
            """)

            # ── Telemetry constraint hits ──────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_constraint_hits (
                    hit_id              BIGSERIAL   PRIMARY KEY,
                    run_id              TEXT        NOT NULL
                                         REFERENCES telemetry_runs(run_id)
                                         ON DELETE CASCADE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    constraint_source   TEXT,
                    constraint_rule     TEXT,
                    file_path           TEXT,
                    issue_type          TEXT,
                    action              TEXT,
                    metadata            JSONB
                )
            """)

            # ── Telemetry static analysis results ─────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_static_analysis (
                    result_id           BIGSERIAL   PRIMARY KEY,
                    run_id              TEXT        NOT NULL
                                         REFERENCES telemetry_runs(run_id)
                                         ON DELETE CASCADE,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    adapter_name        TEXT,
                    file_path           TEXT,
                    findings_count      INTEGER     DEFAULT 0,
                    metrics             JSONB,
                    metadata            JSONB
                )
            """)

            # ── Telemetry usage reports ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_usage_reports (
                    report_id           BIGSERIAL   PRIMARY KEY,
                    report_date         DATE        NOT NULL,
                    report_type         TEXT        NOT NULL,
                    total_runs          INTEGER     DEFAULT 0,
                    total_files         INTEGER     DEFAULT 0,
                    total_findings      INTEGER     DEFAULT 0,
                    total_fixes         INTEGER     DEFAULT 0,
                    total_tokens        INTEGER     DEFAULT 0,
                    estimated_cost_usd  NUMERIC(10,4) DEFAULT 0,
                    top_issue_types     JSONB,
                    top_files           JSONB,
                    metadata            JSONB,
                    UNIQUE(report_date, report_type)
                )
            """)

            # ── Indexes ───────────────────────────────────────────────────
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_mode ON telemetry_runs(mode)",
                "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_created ON telemetry_runs(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_telemetry_events_run ON telemetry_events(run_id)",
                "CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(event_type)",
                "CREATE INDEX IF NOT EXISTS idx_hitl_fd_issue_type ON hitl_feedback_decisions(issue_type)",
                "CREATE INDEX IF NOT EXISTS idx_hitl_fd_file_path ON hitl_feedback_decisions(file_path)",
                "CREATE INDEX IF NOT EXISTS idx_hitl_fd_human_action ON hitl_feedback_decisions(human_action)",
                # New telemetry table indexes
                "CREATE INDEX IF NOT EXISTS idx_findings_run ON telemetry_findings(run_id)",
                "CREATE INDEX IF NOT EXISTS idx_findings_severity ON telemetry_findings(severity)",
                "CREATE INDEX IF NOT EXISTS idx_findings_category ON telemetry_findings(category)",
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON telemetry_llm_calls(run_id)",
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_provider_model ON telemetry_llm_calls(provider, model)",
                "CREATE INDEX IF NOT EXISTS idx_constraint_hits_run ON telemetry_constraint_hits(run_id)",
                "CREATE INDEX IF NOT EXISTS idx_constraint_hits_action ON telemetry_constraint_hits(action)",
                "CREATE INDEX IF NOT EXISTS idx_static_analysis_run ON telemetry_static_analysis(run_id)",
                "CREATE INDEX IF NOT EXISTS idx_usage_reports_date ON telemetry_usage_reports(report_date)",
            ]:
                cur.execute(idx_sql)

            dbconn.commit()
            logger.info("Telemetry & HITL tables ready.")

    def run(self) -> None:
        """
        Execute the complete database and schema setup process.

        Steps:
        1. Connect as admin to 'postgres' DB for role/database setup
        2. Connect to user database for schema/extension setup
        """
        ssl_kwargs = self._get_ssl_kwargs()
        logger.info(
            f"Database setup: host={self.host}, port={self.port}, "
            f"db={self.database}, ssl_mode={self.ssl_mode}, "
            f"remote={self.is_remote()}"
        )

        # 1. Connect as admin to 'postgres' DB for role/database setup
        with psycopg2.connect(
            dbname='postgres',
            user=self.admin_user,
            password=self.admin_password,
            host=self.host,
            port=self.port,
            **ssl_kwargs,
        ) as conn_admin:
            conn_admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                # Ensure role exists
                create_role_if_not_exists(
                    conn_admin,
                    target_user=self.username,
                    target_password=self.password,
                    createdb=True
                )
                # Ensure DB exists
                self.create_db_if_not_exists(conn_admin)
            except Exception as ex:
                logger.error(f"Error during admin setup: {ex}")
                raise

        # 2. Connect to user database for schema/extension setup
        with psycopg2.connect(
            dbname=self.database,
            user=self.admin_user,
            password=self.admin_password,
            host=self.host,
            port=self.port,
            **ssl_kwargs,
        ) as conn_user:
            try:
                self.run_schema_setup(conn_user)
            except Exception as ex:
                logger.error(f"Error during schema setup: {ex}")
                raise
        logger.info("All schema/database setup complete.")


if __name__ == "__main__":
    PostgresDbSetup().run()
