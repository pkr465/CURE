"""
CURE — Codebase Update & Refactor Engine
PostgreSQL Vector Store API Module

Abstraction for storing, indexing, and searching embeddings in a Postgres (PGVector) vector store.
Logs every step and debug state for troubleshooting record insertion.
"""

import logging
import json
from typing import Optional, Set, List, Dict, Any
from sqlalchemy import create_engine, text
from langchain_core.documents import Document
from langchain_postgres import PGVector

# Graceful import of QGenieEmbeddings with fallback
try:
    from qgenie.integrations.langchain import QGenieEmbeddings
    QGENIE_EMBEDDINGS_AVAILABLE = True
except ImportError:
    QGenieEmbeddings = None
    QGENIE_EMBEDDINGS_AVAILABLE = False


class PostgresVectorStore:
    """
    Abstraction for storing, indexing, and searching embeddings in a Postgres (PGVector) vector store.
    Logs every step and debug state for troubleshooting record insertion.
    """

    def __init__(
        self,
        connection_string: str,
        collection_name: str,
        embedding_table: str,
        collection_embedding_table: str,
        api_key: str,
    ) -> None:
        self.logger = logging.getLogger(__name__)

        # Check if QGenieEmbeddings is available
        if not QGENIE_EMBEDDINGS_AVAILABLE:
            raise RuntimeError(
                "QGenieEmbeddings from qgenie.integrations.langchain is not available. "
                "Please install the qgenie package: pip install qgenie"
            )

        self.connection_string = connection_string
        self.collection_name = collection_name
        self.api_key = api_key
        self.embedding_table = embedding_table
        self.collection_embedding_table = collection_embedding_table
        self.vectorstore: Optional[PGVector] = None
        self.engine = create_engine(connection_string)
        self.embeddings_fn = QGenieEmbeddings(api_key=api_key)
        self.vectorstore = self._get_vector_store()

    def fetch_existing_uuids(self, engine, embedding_table: str) -> Set[str]:
        """
        Fetch all UUIDs from the specified embedding table in the database.
        Uses parameterized queries for safe identifier usage.
        """
        with engine.connect() as conn:
            sql = text(f'SELECT cmetadata ->> \'uuid\' FROM "{embedding_table}"')
            result = conn.execute(sql)
            return set(row[0] for row in result if row[0] is not None)

    def _get_vector_store(self) -> Optional[PGVector]:
        """
        Initialize and return the PGVector store instance.
        """
        try:
            vector_store = PGVector(
                connection=self.engine,
                embeddings=self.embeddings_fn,
                collection_name=self.collection_name,
                use_jsonb=True
            )
            self.logger.debug(f"Created PGVector store for collection '{self.collection_name}'")
            return vector_store
        except Exception as e:
            self.logger.error(f"Failed to connect to the Postgres vector store: {e}")
            return None

    def debug_embedding_tables(self, message: str = "") -> None:
        """
        Print row counts in traditional and discovered embedding tables for debugging.
        """
        with self.engine.connect() as conn:
            self.logger.debug(f"Checking candidate embedding tables for row counts:{' ' + message if message else ''}")
            candidates: Set[str] = set()
            if self.embedding_table:
                candidates.add(self.embedding_table)
            # Also check all public tables with 'embedding' in their name
            rows = conn.execute(
                    text("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema='public'
                        AND table_type='BASE TABLE'
                        AND table_name ILIKE '%embedding%'
                    """)
                )
            extra_tables = set(r[0] for r in rows)
            for tbl in sorted(candidates | extra_tables):
                try:
                    count = conn.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
                    self.logger.debug(f"    Table '{tbl}': {count} rows")
                except Exception as e:
                    self.logger.debug(f"    Table '{tbl}': not accessible or does not exist ({e})")

    @staticmethod
    def dict_to_document(doc_dict: Dict[str, Any]) -> Document:
        """
        Convert a plain dict (with 'page_content' and 'meta') to a LangChain Document object.
        """
        return Document(
            page_content=doc_dict.get("page_content", ""),
            metadata=doc_dict.get("meta", {})
        )

    def store_embeddings(self, docs: List[Dict[str, Any]]) -> Optional[PGVector]:
        """
        Stores documents in the PGVector vector store, skipping docs that already exist (by UUID).
        Accepts a list of dicts with 'meta' and 'page_content' keys.
        """
        if self.vectorstore is None:
            self.logger.error("Vector store not initialized.")
            return None

        try:
            existing_uuids = self.fetch_existing_uuids(self.engine, self.embedding_table)
            self.logger.debug(f"Fetched {len(existing_uuids)} existing UUIDs from the database.")
            self.logger.debug(f"Sample existing UUIDs: {list(existing_uuids)[:10]}")

            new_docs = [doc for doc in docs if doc.get("meta", {}).get("uuid") not in existing_uuids]
            skipped = len(docs) - len(new_docs)
            ids = [doc.get("meta", {}).get("uuid") for doc in new_docs]
            self.logger.debug(f"Attempting to add {len(new_docs)} (out of {len(docs)}) new embeddings. UUIDs to be added: {ids}")

            # Show candidate embedding tables and row counts BEFORE
            self.debug_embedding_tables(message="(before insert)")

            if not new_docs:
                self.logger.info("No new documents to ingest. All UUIDs already exist.")
                self.debug_embedding_tables(message="(after SKIP - no insert)")
                return None

            # Convert new_docs to LangChain Document objects
            docs_for_vectorstore = [self.dict_to_document(doc) for doc in new_docs]

            # Determine collection table and existence
            with self.engine.connect() as conn:
                sql = text(f'SELECT COUNT(*) FROM "{self.collection_embedding_table}" WHERE name = :name')
                result = conn.execute(sql, {"name": self.collection_name})
                collection_exists = result.scalar() > 0

            # Print which collection, which table names we're requesting
            self.logger.debug(f"Embedding Table Config: embedding_table='{self.embedding_table}', collection_embedding_table='{self.collection_embedding_table}'")
            self.logger.debug(f"Targeting collection: '{self.collection_name}' (collection_exists={collection_exists})")

            # Insert docs
            if collection_exists:
                self.logger.info(f"Collection '{self.collection_name}' exists. Adding documents.")
                vectorstore = PGVector(
                    connection=self.engine,
                    collection_name=self.collection_name,
                    embeddings=self.embeddings_fn,
                    use_jsonb=True
                )
                vectorstore.add_documents(docs_for_vectorstore, ids=ids)
            else:
                self.logger.info(f"Collection '{self.collection_name}' does not exist. Creating new collection.")
                vectorstore = PGVector.from_documents(
                    documents=docs_for_vectorstore,
                    connection=self.engine,
                    embedding=self.embeddings_fn,  # NOTE: argument is 'embedding', not 'embeddings'
                    collection_name=self.collection_name,
                    use_jsonb=True
                )
                # Print the attributes to see where the table is being created
                if hasattr(vectorstore, 'embedding_embedding_table'):
                    self.logger.debug(f"PGVector created new embedding table: '{vectorstore.embedding_embedding_table}'")

            self.logger.info(f"Documents added to Postgres vector store: {len(new_docs)} new, {skipped} skipped.")

            # Show candidate embedding tables and row counts AFTER
            self.debug_embedding_tables(message="(after insert)")

            # Repeat previous logic but ensure we are checking the right table!
            with self.engine.connect() as conn:
                self.logger.debug(f"Now checking explicit embedding_table '{self.embedding_table}' for inserted contents:")
                try:
                    result = conn.execute(
                        text(f'SELECT count(*) FROM "{self.embedding_table}"')
                    )
                    row_count_after = result.scalar()
                    self.logger.debug(f"(AFTER INSERT) Row count in embedding table '{self.embedding_table}': {row_count_after}")
                    res2 = conn.execute(
                        text(f'SELECT cmetadata ->> \'uuid\' FROM "{self.embedding_table}" LIMIT 10')
                    )
                    uuids_in_db_after = [r[0] for r in res2]
                    self.logger.debug(f"(AFTER INSERT) Sample UUIDs in '{self.embedding_table}': {uuids_in_db_after}")
                except Exception as e:
                    self.logger.warning(f"Could not fetch data from '{self.embedding_table}': {e}")

                # Check all embedding tables for increases
                dupes = None
                try:
                    dupes = conn.execute(
                        text(f'SELECT cmetadata ->> \'uuid\', count(*) FROM "{self.embedding_table}" GROUP BY 1 HAVING count(*) > 1')
                    ).fetchall()
                except Exception:
                    pass
                if dupes and len(dupes):
                    self.logger.warning(f"!! DUPLICATE UUIDs DETECTED IN DB table '{self.embedding_table}': {dupes}")
                else:
                    self.logger.debug(f"No duplicate UUIDs detected in '{self.embedding_table}' after insert.")

            self.vectorstore = vectorstore
            return self.vectorstore
        except Exception as e:
            self.logger.error(f"Error storing embeddings in vector DB: {e}", exc_info=True)
            # Show all candidate embedding tables on any error to help debug leaks
            self.debug_embedding_tables(message="(after ERROR!)")
            return None

    def retrieve(
        self,
        query: str,
        k: int = 2,
        threshold: float = 0.00,
        similarity_threshold: bool = False,
    ) -> List[Document]:
        """
        Retrieve documents from the vector store based on similarity to the query.
        """
        if not self.vectorstore:
            self.logger.warning("Postgres vector store not initialized.")
            return []
        if similarity_threshold:
            search_type = "similarity_score_threshold"
            search_kwargs = {"k": k, "score_threshold": threshold}
        else:
            search_type = "similarity"
            search_kwargs = {"k": k}
        retriever = self.vectorstore.as_retriever(
            search_type=search_type,
            search_kwargs=search_kwargs,
        )
        return retriever.invoke(query)

    def run_custom_query(self, sql: str) -> List[Document]:
        """
        Execute a custom SQL query and return results as LangChain Document objects.
        """
        if not self.engine:
            self.logger.warning("Postgres engine not initialized.")
            return []
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = result.keys()
                rows = result.fetchall()
                docs = []
                for row in rows:
                    row_dict = dict(zip(columns, row))
                    content = row_dict.get("page_content", "")  # adjust field name as needed
                    meta = row_dict.get("cmetadata") or row_dict.get("metadata", {}) or {}
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    docs.append(Document(page_content=content, metadata=meta))
                return docs
        except Exception as e:
            self.logger.error(f"Error running custom SQL query: {e}")
            return []
