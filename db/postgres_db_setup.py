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

            # Optional: Add indexes for faster queries (uncomment if needed)
            # cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS idx_embedding_vector ON {} USING ivfflat (embedding);").format(sql.Identifier(self.embedding_table)))
            # cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS idx_collection_id ON {} (collection_id);").format(sql.Identifier(self.embedding_table)))
            dbconn.commit()
            logger.info("Vector schema/tables ready.")

    def run(self) -> None:
        """
        Execute the complete database and schema setup process.

        Steps:
        1. Connect as admin to 'postgres' DB for role/database setup
        2. Connect to user database for schema/extension setup
        """
        # 1. Connect as admin to 'postgres' DB for role/database setup
        with psycopg2.connect(
            dbname='postgres',
            user=self.admin_user,
            password=self.admin_password,
            host=self.host,
            port=self.port
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
            port=self.port
        ) as conn_user:
            try:
                self.run_schema_setup(conn_user)
            except Exception as ex:
                logger.error(f"Error during schema setup: {ex}")
                raise
        logger.info("All schema/database setup complete.")


if __name__ == "__main__":
    PostgresDbSetup().run()
