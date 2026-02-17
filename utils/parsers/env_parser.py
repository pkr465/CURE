"""
Environment configuration loader and validator.

Loads configuration from .env files and environment variables with:
- Type-safe access (str, bool, int, float, list, path)
- Path normalization and expansion
- Required-key validation with clear error messages
- Default values with fallback chain
- Immutable snapshot after load
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)


class EnvConfig:
    """
    Centralized environment configuration with validation and type coercion.

    Usage:
        config = EnvConfig()                       # auto-discovers .env
        config = EnvConfig(env_file="/path/.env")   # explicit path
        config = EnvConfig(required=["QGENIE_API_KEY"])

        model = config.get("LLM_MODEL", "anthropic::claude-sonnet-4-20250514")
        debug = config.get_bool("DEBUG", False)
        port  = config.get_int("POSTGRES_PORT", 5432)
        ids   = config.get_list("EMAIL_ID")
        path  = config.get_path("OUT_DIR", "./out")
    """

    # --- Known key categories ---

    KEYS: List[str] = [
        # Paths
        "INPUTPATH", "OUTPUTPATH", "OUT_DIR", "FLAT_JSON_PATH", "REPORT_JSON_PATH",
        "GRAPH_PATH", "PDF_PATH", "WORD_DOC_FOLDER", "IMG_DIR", "SOURCE_DIR",
        "GENERATED_MD_DIR", "DOC_FILES", "CODE_BASE_PATH",
        "CHAT_PROMPT_FILE_PATH", "PROMPT_FILE_PATH",
        # LLM / API
        "QGENIE_API_KEY", "ANTHROPIC_API_KEY",
        "QGENIE_CHAT_ENDPOINT",
        "LLM_MODEL", "STREAMLIT_MODEL", "CODING_LLM_MODEL",
        "EMBEDDING_MODEL", "LLM_PROVIDER",
        # Logging / feature flags
        "LOG_LEVEL", "EMAIL_ID", "FORMAT",
        "TOC", "HTML", "KEEP_IMG_DIMS", "RECALC_IMG_DIMS",
        "RECALC_MAX_DIMS", "VERBOSE", "DEBUG",
        # Email / SMTP (Added to reflect .env)
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        "SMTP_USE_TLS", "SMTP_SENDER_EMAIL", "SMTP_SENDER_NAME",
        # Postgres
        "POSTGRES_CONNECTION", "POSTGRES_ADMIN_USERNAME", "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_COLLECTION", "POSTGRES_COLLECTION_TABLENAME",
        "POSTGRES_EMBEDDING_TABLENAME", "POSTGRES_STORE_NAME",
        "POSTGRES_USERNAME", "POSTGRES_PASSWORD",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DATABASE",
        # Mermaid / PDF
        "WITH_PANDOC", "WITH_WMF2SVG", "MMDC_PATH",
        # Vector DB
        "VECTOR_DATABASE",
        # Excel colors
        "PASS_COLOR", "FAIL_COLOR", "ALT_ROW_COLOR",
    ]

    PATH_KEYS: Set[str] = {
        "INPUTPATH", "OUTPUTPATH", "OUT_DIR", "WORD_DOC_FOLDER", "IMG_DIR",
        "SOURCE_DIR", "GENERATED_MD_DIR", "PROMPT_FILE_PATH",
        "CHAT_PROMPT_FILE_PATH", "CODE_BASE_PATH",
        "FLAT_JSON_PATH", "REPORT_JSON_PATH", "GRAPH_PATH", "PDF_PATH",
    }

    BOOL_KEYS: Set[str] = {
        "TOC", "HTML", "KEEP_IMG_DIMS", "RECALC_IMG_DIMS", "VERBOSE", "DEBUG",
        "SMTP_USE_TLS",
    }

    INT_KEYS: Set[str] = {
        "RECALC_MAX_DIMS", "POSTGRES_PORT", "SMTP_PORT",
    }

    DEFAULTS: Dict[str, Any] = {
        "FORMAT": "docx",
        "EMBEDDING_MODEL": "qgenie",
        "LOG_LEVEL": "INFO",
        "LLM_PROVIDER": "anthropic",
        "LLM_MODEL": "anthropic::claude-sonnet-4-20250514",
        "CODING_LLM_MODEL": "anthropic::claude-sonnet-4-20250514",
        "QGENIE_CHAT_ENDPOINT": "https://qgenie-chat.qualcomm.com",
        "RECALC_MAX_DIMS": 500,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": 5432,
        "TOC": False,
        "HTML": False,
        "KEEP_IMG_DIMS": False,
        "RECALC_IMG_DIMS": False,
        "VERBOSE": False,
        "DEBUG": False,
        "PASS_COLOR": "C6EFCE",
        "FAIL_COLOR": "FFC7CE",
        "ALT_ROW_COLOR": "F3F3F3",
        "VECTOR_DATABASE": "postgres",
        # Default SMTP Settings
        "SMTP_PORT": 587,
        "SMTP_USE_TLS": True,
    }

    def __init__(
        self,
        env_file: Optional[str] = None,
        required: Optional[List[str]] = None,
        auto_load: bool = True,
    ):
        self.config: Dict[str, Any] = {}
        self._env_file_used: Optional[str] = None
        self._required = required or []

        if auto_load:
            self.config = self.load_env(env_file)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_env(self, env_file: Optional[str] = None) -> Dict[str, Any]:
        """Load, normalize, and validate environment variables."""
        self._load_dotenv(env_file)

        cfg: Dict[str, Any] = {}

        # Pull all known keys
        for key in self.KEYS:
            raw = os.getenv(key, self.DEFAULTS.get(key))
            cfg[key] = self._strip_quotes(raw)

        # Normalize types
        for key in self.BOOL_KEYS:
            cfg[key] = self._to_bool(cfg.get(key), self.DEFAULTS.get(key, False))

        for key in self.INT_KEYS:
            cfg[key] = self._to_int(cfg.get(key), self.DEFAULTS.get(key, 0))

        for key in self.PATH_KEYS:
            val = cfg.get(key)
            if val and isinstance(val, (str, Path)):
                try:
                    cfg[key] = str(Path(str(val)).expanduser().resolve())
                except Exception:
                    pass

        # EMAIL_ID normalization
        email = cfg.get("EMAIL_ID")
        if isinstance(email, list):
            cfg["EMAIL_ID"] = ",".join(str(e) for e in email)
        elif email is not None and not isinstance(email, str):
            cfg["EMAIL_ID"] = str(email)

        # Validation
        self._validate(cfg)

        return cfg

    def _load_dotenv(self, env_file: Optional[str] = None) -> None:
        """Load .env file using dotenv, with fallback to manual parsing."""
        try:
            from dotenv import load_dotenv, find_dotenv

            if env_file:
                target = env_file
            else:
                target = find_dotenv(usecwd=True)

            if target and os.path.isfile(target):
                load_dotenv(target)
                self._env_file_used = target
                logger.info(f"Loaded .env from: {target}")
            else:
                logger.info("No .env file found; using system environment only.")
        except ImportError:
            # dotenv not installed — try manual parsing
            if env_file and os.path.isfile(env_file):
                self._parse_env_file(env_file)
                self._env_file_used = env_file
            else:
                logger.info("python-dotenv not installed and no env_file provided.")

    @staticmethod
    def _parse_env_file(filepath: str) -> None:
        """Minimal .env parser for when python-dotenv is unavailable."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        os.environ.setdefault(key, value)
        except Exception as e:
            logger.warning(f"Failed to parse env file {filepath}: {e}")

    def _validate(self, cfg: Dict[str, Any]) -> None:
        """Validate required keys are present."""
        missing = [k for k in self._required if not cfg.get(k)]
        if missing:
            msg = f"Missing required environment variables: {missing}"
            logger.error(msg)
            raise ValueError(msg)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value with optional default."""
        val = self.config.get(key)
        if val is None or val == "":
            return default if default is not None else self.DEFAULTS.get(key)
        return val

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean configuration value."""
        return self._to_bool(self.config.get(key), default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer configuration value."""
        return self._to_int(self.config.get(key), default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get a float configuration value."""
        val = self.config.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_list(self, key: str, separator: str = ",", default: Optional[List[str]] = None) -> List[str]:
        """Get a comma-separated value as a list of trimmed strings."""
        val = self.config.get(key, "")
        if not val:
            return default or []
        if isinstance(val, list):
            return val
        return [item.strip() for item in str(val).split(separator) if item.strip()]

    def get_path(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a path value, resolved and expanded."""
        val = self.config.get(key) or default
        if val:
            return str(Path(str(val)).expanduser().resolve())
        return None

    def has(self, key: str) -> bool:
        """Check if a key is set and non-empty."""
        val = self.config.get(key)
        return val is not None and val != ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a copy of the full configuration dict."""
        return dict(self.config)

    def __repr__(self) -> str:
        loaded = self._env_file_used or "system env"
        return f"EnvConfig(source='{loaded}', keys={len(self.config)})"

    # ------------------------------------------------------------------
    # Type conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_quotes(value: Any) -> Any:
        """Strip surrounding quotes from a string value."""
        if isinstance(value, str):
            stripped = value.strip().strip("'\"")
            return stripped if stripped else None
        return value

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        """Convert a value to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Convert a value to int."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default