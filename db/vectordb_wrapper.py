"""
CURE — Codebase Update & Refactor Engine
Vector DB Wrapper: high-level interface for vector store operations.
"""

import logging
from typing import Any, Dict, List, Optional

from db.postgres_api import PostgresVectorStore


class VectorDB:
    """
    High-level interface for vector store operations.

    Currently supports PostgreSQL (PGVector) backend. The backend is selected
    via the ``VECTOR_DATABASE`` key in the config (default: ``postgres``).
    """

    def __init__(self, env_config: Dict[str, Any]) -> None:
        """
        Initialize based on env_config and instantiate the underlying store.
        Args:
            env_config (dict): Environment and connection parameters
        """
        self.env_config = env_config
        self.logger = logging.getLogger(__name__)
        self.vector_db = env_config.get("VECTOR_DATABASE", "postgres").lower()
        self.vector_store = self._initialize_store()

    def _initialize_store(self) -> Optional[PostgresVectorStore]:
        """
        Create and return an instance of the configured vector store backend.
        """
        db = self.vector_db
        if db == "postgres":
            pg_vector_store = PostgresVectorStore(
                self.env_config.get("POSTGRES_CONNECTION"),
                self.env_config.get("POSTGRES_COLLECTION"),
                self.env_config.get("POSTGRES_EMBEDDING_TABLENAME"),
                self.env_config.get("POSTGRES_COLLECTION_EMBEDDING_TABLENAME"),
                self.env_config.get("QGENIE_API_KEY")
            )
            return pg_vector_store
        else:
            raise ValueError(f"Unknown vector backend: {db}")

    def store_embeddings(self, docs: List[Dict[str, Any]]) -> Any:
        """
        Store embeddings for a list of documents.

        :param docs: Documents to embed and store (dicts with 'page_content' and 'meta').
        :returns: Vector store instance or None.
        """
        if self.vector_store is None:
            raise RuntimeError("Vector store not initialized")
        return self.vector_store.store_embeddings(docs)

    def retrieve(
        self,
        query: str,
        k: int = 2,
        threshold: float = 0.0,
        similarity_threshold: bool = False,
    ) -> List[Any]:
        """
        Retrieve most similar documents to the given query.

        :param query: Text query for similarity search.
        :param k: Number of results to return.
        :param threshold: Optional similarity threshold.
        :param similarity_threshold: Whether to use threshold filtering.
        :returns: List of Document objects.
        """
        if self.vector_store is None:
            raise RuntimeError("Vector store not initialized")
        return self.vector_store.retrieve(query, k, threshold, similarity_threshold)
    
    def run_custom_query(self, sql: str) -> List[Any]:
        """
        Execute a custom SQL query (Postgres backend only).

        :param sql: SQL statement to execute.
        :returns: List of Document objects.
        """
        if self.vector_store is None:
            raise RuntimeError("Vector store not initialized")
        if self.vector_db == "postgres":
            return self.vector_store.run_custom_query(sql)
        else:
            raise NotImplementedError("Custom SQL queries are only supported for the Postgres backend.")
      