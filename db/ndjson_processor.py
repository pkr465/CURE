"""
NDJSON Processor for Embedding-Ready Documents

CURE — Codebase Update & Refactor Engine

This module processes NDJSON files to generate embedding-ready documents with:
  - Stable UUID generation based on configurable key combinations
  - Flexible metadata extraction and preservation
  - Rich page content construction from record fields
  - Graceful handling of optional embedding dependencies
"""

import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Iterable

# Gracefully handle optional QGenieEmbeddings dependency
try:
    from qgenie.integrations.langchain import QGenieEmbeddings
    QGENIE_EMBEDDINGS_AVAILABLE = True
except ImportError:
    QGenieEmbeddings = None
    QGENIE_EMBEDDINGS_AVAILABLE = False

# Gracefully handle optional GlobalConfig dependency
try:
    from utils.parsers.global_config_parser import GlobalConfig
    GLOBAL_CONFIG_AVAILABLE = True
except ImportError:
    GlobalConfig = None
    GLOBAL_CONFIG_AVAILABLE = False

logger = logging.getLogger(__name__)


class NDJSONProcessor:
    """
    Processor for NDJSON files to generate embedding-ready documents.

    This version is tuned for flattened health report records:
      - Each NDJSON line is one record.
      - We try to produce ONE embedding document per record.
      - We build rich page_content from many common fields.
      - We build a stable UUID from id / record_type / module / file path.
    """

    def __init__(
        self,
        ndjson_path: str,
        env_config: Optional[Dict[str, Any]] = None,
    ):
        self.ndjson_path = Path(ndjson_path)
        self.env_config = env_config or {}

        # Parse configured fields (if any), but fall back to robust defaults
        # suitable for your flattened healthreport.
        self.vector_db_fields = self.parse_fields(
            self.env_config.get("VECTOR_DB_FIELDS")
        )
        self.metadata_fields = self.parse_fields(
            self.env_config.get("VECTOR_DB_METADATA_FIELDS")
        )
        self.uuid_keys = self.parse_fields(
            self.env_config.get("VECTOR_DB_UUID_KEYS")
        )

        # If not configured, choose sensible defaults:
        if not self.vector_db_fields:
            # Textual / summary fields to build page_content from.
            self.vector_db_fields = [
                "record_type",
                "id",
                "source",
                "title",
                "name",
                "summary",
                "description",
                "details",
                "message",
                "reason",
                "recommendation",
                "overall_recommendation",
                "status",
                "severity",
                "category",
                "module",
                "file_relative_path",
                "file_name",
                "function",
                "metric_name",
                "violation_type",
                "violation_message",
                "notes",
            ]

        if not self.metadata_fields:
            # Metadata we want to keep for filtering / context.
            self.metadata_fields = [
                "record_type",
                "id",
                "source",
                "source_file",
                "file_relative_path",
                "file_name",
                "module",
                "dependencies",
                "depends_on",
                "overall_score",
                "overall_grade",
                "dependency_score",
                "quality_score",
                "complexity_score",
                "maintainability_score",
                "documentation_score",
                "test_coverage_score",
                "security_score",
                "status",
                "severity",
                "category",
                "metric_value",
                "line",
                "function",
                "violation_type",
            ]

        if not self.uuid_keys:
            # Use multiple keys to make UUID stable & unique.
            self.uuid_keys = [
                "id",
                "record_type",
                "module",
                "file_relative_path",
                "file_name",
            ]

        # Additional compact metadata fields we always keep in meta["cmeta"]
        self.cmeta_fields = [
            "file_relative_path",
            "file_name",
            "module",
            "dependencies",
            "depends_on",
        ]

        logger.info(f"VECTOR_DB_FIELDS: {self.vector_db_fields}")
        logger.info(f"VECTOR_DB_METADATA_FIELDS: {self.metadata_fields}")
        logger.info(f"VECTOR_DB_UUID_KEYS: {self.uuid_keys}")

    @staticmethod
    def parse_fields(value: Any) -> List[str]:
        """
        Parse a comma-separated string or list into a list of field names.
        """
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return []

    def default_cmeta_extractor(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract compact metadata from a record for cmeta.
        """
        cmeta = {}
        for field in self.cmeta_fields:
            if field in entry:
                cmeta[field] = entry[field]
        return cmeta

    def extract_meta(self, entry: Any) -> Dict[str, Any]:
        """
        Extract metadata dictionary for a record based on metadata_fields.
        If metadata_fields is empty, we keep all top-level keys except some
        obvious content / internal fields.
        """
        if not isinstance(entry, dict):
            # Defensive: if something weird gets through, just return empty.
            return {}

        if self.metadata_fields:
            meta = {}
            for k in self.metadata_fields:
                if k in entry:
                    v = entry[k]
                    # Normalize lists/dicts to JSON string for metadata
                    if isinstance(v, (list, dict)):
                        try:
                            v = json.dumps(v, ensure_ascii=False)
                        except Exception:
                            v = str(v)
                    meta[k] = v
        else:
            # Fallback: keep everything except some noisy fields
            meta = {}
            exclude = {"text", "content", "page_content", "embedding"}
            for k, v in entry.items():
                if k in exclude:
                    continue
                if isinstance(v, (list, dict)):
                    try:
                        v = json.dumps(v, ensure_ascii=False)
                    except Exception:
                        v = str(v)
                meta[k] = v

        return meta

    def deterministic_doc_uuid(self, entry: Dict[str, Any]) -> str:
        """
        Build a deterministic UUID for a record based on configured uuid_keys.
        Falls back to hashing the entire entry if necessary.
        """
        # Try to use uuid field directly if present
        if "uuid" in entry and entry["uuid"]:
            return str(entry["uuid"])

        # Build a composite key from uuid_keys
        key_parts: List[str] = []
        for k in self.uuid_keys:
            v = entry.get(k)
            if v is not None:
                key_parts.append(f"{k}={v}")
        if key_parts:
            composite = "|".join(key_parts)
        else:
            # Fallback: hash the whole entry
            try:
                composite = json.dumps(entry, sort_keys=True, ensure_ascii=False)
            except Exception:
                composite = str(entry)

        md5 = hashlib.md5(composite.encode("utf-8")).hexdigest()
        return md5

    def load_ndjson_file(self) -> List[Dict[str, Any]]:
        """
        Load the NDJSON file into a list of dict records.
        """
        if not self.ndjson_path.exists():
            raise FileNotFoundError(f"NDJSON file not found: {self.ndjson_path}")

        entries: List[Dict[str, Any]] = []
        with self.ndjson_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as e:
                    logger.warning(
                        f"Skipping unparsable line {line_num} in {self.ndjson_path}: {e}"
                    )
                    continue
                if not isinstance(obj, dict):
                    logger.warning(
                        f"Skipping non-dict JSON at line {line_num} in {self.ndjson_path}: "
                        f"{type(obj).__name__}"
                    )
                    continue
                entries.append(obj)

        logger.info(
            f"Loaded {len(entries)} entries from NDJSON file: {self.ndjson_path}"
        )
        return entries

    def construct_page_content(self, entry: Dict[str, Any]) -> str:
        """
        Build human-readable page content from an entry.

        Strategy:
          - If VECTOR_DB_FIELDS configured: use them in "Label: value" lines.
          - Otherwise: use a heuristic set of keys.
        """
        # If custom vector fields configured, use them
        fields = self.vector_db_fields or [
            # Fallback keys if none were provided
            "record_type",
            "id",
            "source",
            "title",
            "name",
            "summary",
            "description",
            "details",
            "message",
            "reason",
            "recommendation",
            "overall_recommendation",
            "module",
            "file_relative_path",
            "file_name",
        ]

        lines: List[str] = []

        # Optionally prepend a heading
        record_type = entry.get("record_type")
        rid = entry.get("id")
        if record_type or rid:
            heading = f"Record Type: {record_type or ''}"
            if rid:
                heading += f" | Id: {rid}"
            lines.append(heading)

        # Add each field if present
        for field in fields:
            if field in ("record_type", "id"):
                # already covered in heading
                continue
            if field not in entry:
                continue
            value = entry[field]
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                try:
                    value = json.dumps(value, ensure_ascii=False)
                except Exception:
                    value = str(value)
            else:
                value = str(value)
            if not value.strip():
                continue
            label = field.replace("_", " ").title()
            lines.append(f"{label}: {value}")

        content = "\n".join(lines).strip()
        return content

    def generate_records(self) -> List[Dict[str, Any]]:
        """
        Main entry point: read NDJSON and produce embedding-ready docs.

        Returns:
            List of documents, each a dict with:
              - meta: metadata (including 'uuid' and 'ingestion_timestamp')
              - cmeta: compact metadata
              - page_content: text to embed
              - uuid: deterministic identifier
        """
        entries = self.load_ndjson_file()
        docs_out: List[Dict[str, Any]] = []

        now = datetime.now(timezone.utc).isoformat()
        source_file = self.ndjson_path.name

        for idx, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                logger.warning(
                    f"Skipping non-dict entry at index {idx} from NDJSON parsing."
                )
                continue

            meta = self.extract_meta(entry)
            cmeta = self.default_cmeta_extractor(entry)
            uuid = self.deterministic_doc_uuid(entry)
            page_content = self.construct_page_content(entry)

            if not page_content:
                # In practice, with the above defaults, this should rarely happen.
                logger.debug(
                    f"Entry {idx} has empty page_content; still embedding to keep 1:1 mapping."
                )

            # Enrich meta with required fields
            meta["uuid"] = uuid
            meta["ingestion_timestamp"] = now
            meta.setdefault("source_file", source_file)

            doc = {
                "meta": meta,
                "cmeta": cmeta,
                "page_content": page_content,
                "uuid": uuid,
            }
            docs_out.append(doc)

        logger.info(
            f"Processed {len(docs_out)} valid documents for embedding. "
            f"Skipped {len(entries) - len(docs_out)} docs with parsing issues."
        )
        return docs_out

    @staticmethod
    def get_embedding_function(env_config: Optional[Dict[str, Any]] = None):
        """
        Returns a QGenieEmbeddings instance configured from env_config or GlobalConfig,
        or raises if not available.

        Configuration precedence:
          1. Try GlobalConfig first (if available and configured)
          2. Fall back to env_config parameters
          3. Raise if neither QGenieEmbeddings is available nor config is sufficient

        Args:
            env_config: Optional dict with QGENIE_EMBEDDINGS_API_KEY and QGENIE_EMBEDDINGS_MODEL

        Returns:
            QGenieEmbeddings instance

        Raises:
            ImportError: If QGenieEmbeddings is not available
            RuntimeError: If required configuration is missing
        """
        if not QGENIE_EMBEDDINGS_AVAILABLE:
            raise ImportError(
                "QGenieEmbeddings is not available. "
                "Please install qgenie: pip install qgenie"
            )

        env_config = env_config or {}
        api_key = None
        embed_model = "text-embedding-3-small"

        # Try GlobalConfig first if available
        if GLOBAL_CONFIG_AVAILABLE and GlobalConfig:
            try:
                config = GlobalConfig()
                api_key = config.get("QGENIE_EMBEDDINGS_API_KEY")
                embed_model = config.get("QGENIE_EMBEDDINGS_MODEL") or embed_model
                logger.debug("Using embedding config from GlobalConfig")
            except Exception as e:
                logger.debug(f"Could not load embedding config from GlobalConfig: {e}")

        # Fall back to env_config if not found in GlobalConfig
        if not api_key:
            api_key = env_config.get("QGENIE_EMBEDDINGS_API_KEY")
            embed_model = env_config.get("QGENIE_EMBEDDINGS_MODEL") or embed_model
            if api_key:
                logger.debug("Using embedding config from env_config")

        if not api_key:
            raise RuntimeError(
                "QGENIE_EMBEDDINGS_API_KEY is not configured. "
                "Set it in GlobalConfig or pass via env_config parameter."
            )

        return QGenieEmbeddings(
            api_key=api_key,
            model=embed_model,
        )
