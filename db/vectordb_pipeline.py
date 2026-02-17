"""
CURE Vector Database Pipeline
Orchestrates the vector DB embedding pipeline for CURE health metrics.
"""
import os
import logging
from pathlib import Path
from datetime import datetime
import hashlib
import traceback
from typing import Union

try:
    from utils.parsers.global_config_parser import GlobalConfig
except ImportError:
    GlobalConfig = None
from utils.parsers.env_parser import EnvConfig
from db.postgres_api import PostgresVectorStore
from db.ndjson_processor import NDJSONProcessor


class VectorDbPipeline:
    """
    CURE: Orchestrates the vector DB embedding pipeline:
    - Loads configuration from .env/environment or GlobalConfig
    - Sets up logging
    - Computes and checks input data hashes
    - Invokes embedding and database storage
    - Maintains embedding result log

    Expected upstream:
    - healthreport.json -> flattened JSON -> NDJSON (one record per line)
    - NDJSON files located under NDJSON_PATH or REPORT_JSON_PATH
    """

    def __init__(self, environment=None):
        if environment is None:
            if GlobalConfig is not None:
                try:
                    self.env_config = GlobalConfig()
                except Exception:
                    self.env_config = EnvConfig()
            else:
                self.env_config = EnvConfig()
        else:
            self.env_config = environment
        
        self.connection_string   = self.env_config.get("POSTGRES_CONNECTION")
        self.collection_name     = self.env_config.get("POSTGRES_COLLECTION")
        self.collection_table    = self.env_config.get("POSTGRES_COLLECTION_TABLENAME")
        self.embedding_table     = self.env_config.get("POSTGRES_EMBEDDING_TABLENAME")
        self.api_key             = self.env_config.get("QGENIE_API_KEY")
        self.admin_username      = self.env_config.get("POSTGRES_ADMIN_USERNAME")
        self.admin_password      = self.env_config.get("POSTGRES_ADMIN_PASSWORD")
        self.pg_store_name       = self.env_config.get("POSTGRES_STORE_NAME")
        self.username            = self.env_config.get("POSTGRES_USERNAME")
        self.database            = self.env_config.get("POSTGRES_DATABASE")
        self.password            = self.env_config.get("POSTGRES_PASSWORD")
        self.host                = self.env_config.get("POSTGRES_HOST")
        self.port                = int(self.env_config.get("POSTGRES_PORT", "5432"))  # Ensure integer type for port
        self.chunk_size          = 500  # Default chunk size

        self.setup_logging(self.env_config.get("LOG_LEVEL", "INFO"))
        self.logger = logging.getLogger(__name__)

    def get_repo_root(self) -> Path:
        """Returns the repository root path."""
        return Path(__file__).resolve().parent.parent

    def resolve_relative_path(self, relative_path: Union[str, Path]) -> Path:
        """Resolves a path relative to the repository root."""
        p = Path(relative_path)
        if p.is_absolute():
            return p
        return self.get_repo_root() / p

    @staticmethod
    def setup_logging(log_level):
        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    @staticmethod
    def chunked(iterable, n):
        for i in range(0, len(iterable), n):
            yield iterable[i:i + n]

    @staticmethod
    def get_file_hash(filepath):
        h = hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    h.update(chunk)
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to compute hash for {filepath}: {e}")
            return None
        return h.hexdigest()

    def embed_and_log_results_chunked(self, docs, vector_store, embedding_results_log, chunk_size=500):
        results, failed = [], []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(embedding_results_log, "a") as logf:
            logf.write(f"{ts} | VectorDB batch: {len(docs)} records (chunk size {chunk_size})\n")
            for idx, batch in enumerate(self.chunked(docs, chunk_size)):
                try:
                    vector_store.store_embeddings(batch)
                    for doc in batch:
                        meta = doc.get("meta", {})
                        # Prefer module name; fall back to metric_name or placeholder
                        name = meta.get("module") or meta.get("metric_name") or "<no name>"
                        logf.write(f"SUCCESS | {name}\n")
                        results.append(doc)
                except Exception as exc:
                    msg = f"FAILED  | CHUNK {idx} | {exc}\n{traceback.format_exc()}\n"
                    self.logger.error(msg.strip())
                    for doc in batch:
                        meta = doc.get("meta", {})
                        name = meta.get("module") or meta.get("metric_name") or "<no name>"
                        logf.write(f"FAILED  | {name} | Batch error: {exc}\n")
                        failed.append((doc, str(exc)))
        self.logger.info(f"{len(results)} docs embedded successfully. {len(failed)} failed.")
        return results, failed
    
    def embed_and_log_results(self, docs, vector_store, embedding_results_log):
        results, failed = [], []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(embedding_results_log, "a") as logf:
            logf.write(f"{ts} | VectorDB batch: {len(docs)} records\n")
            for doc in docs:
                meta = doc.get("meta", {})
                name = meta.get("module") or meta.get("metric_name") or "<no name>"
                try:
                    vector_store.store_embeddings([doc])
                    logf.write(f"SUCCESS | {name}\n")
                    results.append(doc)
                except Exception as exc:
                    msg = f"FAILED  | {name} | {exc}\n{traceback.format_exc()}\n"
                    self.logger.error(msg.strip())
                    logf.write(msg)
                    failed.append((doc, str(exc)))
        self.logger.info(f"{len(results)} docs embedded successfully. {len(failed)} failed.")
        return results, failed

    def verify_input_and_hash(self, json_fpath: Path, log_dir: Path):
        embedding_results_log = log_dir / "embedding_results_log.txt"
        embedding_results_log.parent.mkdir(parents=True, exist_ok=True)
        hash_file = Path(str(json_fpath) + ".md5")
        new_hash = self.get_file_hash(json_fpath)
        old_hash = hash_file.read_text().strip() if hash_file.exists() else ""
        if new_hash is None:
            self.logger.error(f"Could not compute hash for input file: {json_fpath}")
            return False, embedding_results_log
        if new_hash == old_hash:
            self.logger.info(
                f"Input file has not changed since last run, skipping embedding step. (current hash: {new_hash})"
            )
            return False, embedding_results_log
        else:
            self.logger.info(
                f"Input file changed: old hash={old_hash or '<none>'} new hash={new_hash}. Proceeding with embedding update."
            )
            hash_file.write_text(new_hash)
        return True, embedding_results_log

    def run(self):
        self.logger.debug(f"vectordb_agent CWD is: {os.getcwd()}")
        self.logger.info("Starting vector DB data pipeline...")

        processed_log = self.env_config.get("PROCESSED_LOG")
        processed_log_path = Path(processed_log) if processed_log else Path.cwd()
        self.logger.debug(f"Processed log path: {processed_log_path}")

        # NDJSON directory:
        # Prefer NDJSON_PATH; fallback to REPORT_JSON_PATH for backward compatibility
        ndjson_dir_raw = self.env_config.get("NDJSON_PATH") or self.env_config.get("REPORT_JSON_PATH")
        ndjson_dir = self.resolve_relative_path(ndjson_dir_raw)
        self.logger.info(f"NDJSON input dir = {ndjson_dir}")
        os.makedirs(ndjson_dir, exist_ok=True)

        # Find first .ndjson file
        selected_ndjson_file = None
        for filename in os.listdir(ndjson_dir):
            if filename.endswith(".ndjson"):
                selected_ndjson_file = os.path.join(ndjson_dir, filename)
                break  # Only process the first matching .ndjson

        if not selected_ndjson_file or not Path(selected_ndjson_file).exists():
            self.logger.error(f"No matching .ndjson input file found in: {ndjson_dir}")
            return

        log_dir = Path(selected_ndjson_file).parent
        proceed, embedding_results_log = self.verify_input_and_hash(Path(selected_ndjson_file), log_dir)
        if not proceed:
            return

        self.logger.info(f"Reading labeled docs from: {selected_ndjson_file}")
        # Use the selected .ndjson file for NDJSONProcessor
        processor = NDJSONProcessor(selected_ndjson_file, env_config=self.env_config)
        records = processor.generate_records()    # records is a list of dicts

        if not records:
            self.logger.warning("No documents could be generated from the provided NDJSON for embedding/storage.")
            with open(embedding_results_log, "a") as logf:
                logf.write(f"{datetime.now()} | WARNING | No documents to embed from {selected_ndjson_file}\n")
            return
            
        # For debugging/trace: print a sample
        self.logger.info(f"First 2 generated records: {records[:2]}")

        doc_count = len(records)
        self.logger.info(f"Documents generated for embedding: {doc_count}")

        try:
            vector_store = PostgresVectorStore(
                self.connection_string,
                self.collection_name,
                self.embedding_table,
                self.collection_table,
                self.api_key
            )
        except Exception as e:
            self.logger.error(f"Failed to initialize vector store: {e}")
            with open(embedding_results_log, "a") as logf:
                logf.write(f"{datetime.now()} | FATAL | Vector store init error: {e}\n")
            return

        # Embed and store in batches
        if doc_count <= self.chunk_size:
            results, failed = self.embed_and_log_results(records, vector_store, embedding_results_log)
        else:
            results, failed = self.embed_and_log_results_chunked(records, vector_store, embedding_results_log, chunk_size=self.chunk_size)

        self.logger.info(f"Embedding/storage finished. Results: {len(results)}, Failed: {len(failed)}")

def main():
    pipeline = VectorDbPipeline()
    pipeline.run()

if __name__ == "__main__":
    main()