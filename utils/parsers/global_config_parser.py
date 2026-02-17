# global_config_parser.py
"""
YAML-based global configuration loader and validator.

Loads configuration from a structured global_config.yaml file with:
- Hierarchical section-based access (paths.out_dir, llm.model, database.port)
- Environment variable interpolation (${ENV_VAR} syntax)
- Type-safe accessor methods (str, bool, int, float, list, path)
- Path normalization and expansion
- Required-key validation
- Full backward compatibility with EnvConfig flat-key interface
- Merge support: layer multiple YAML files (base + env-specific overrides)

Dependencies: PyYAML (pip install pyyaml)
Fallback:     stdlib-only basic YAML parser for simple key: value files

Usage:
    from utils.parsers.global_config_parser import GlobalConfig

    # Auto-discover global_config.yaml
    config = GlobalConfig()

    # Explicit file
    config = GlobalConfig(config_file="./config/production.yaml")

    # Hierarchical access
    model    = config.get("llm.model")
    port     = config.get_int("database.port", 5432)
    debug    = config.get_bool("logging.debug", False)
    out_dir  = config.get_path("paths.out_dir")
    patterns = config.get_list("dependency_builder.ccls_ignore_patterns")

    # Flat-key backward compatibility (works like EnvConfig)
    model    = config.get("LLM_MODEL")  # maps to llm.model
    port     = config.get("POSTGRES_PORT")  # maps to database.port

    # Section access
    db_config = config.get_section("database")
    # -> {"host": "localhost", "port": 5432, ...}
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flat-key to YAML-path mapping (backward compatibility with EnvConfig)
# ---------------------------------------------------------------------------

FLAT_KEY_MAP: Dict[str, str] = {
    # Paths
    "INPUTPATH":              "paths.input_path",
    "OUTPUTPATH":             "paths.output_path",
    "OUT_DIR":                "paths.out_dir",
    "FLAT_JSON_PATH":         "paths.flat_json_path",
    "REPORT_JSON_PATH":       "paths.report_json_path",
    "GRAPH_PATH":             "paths.graph_path",
    "PDF_PATH":               "paths.pdf_path",
    "WORD_DOC_FOLDER":        "paths.word_doc_folder",
    "IMG_DIR":                "paths.img_dir",
    "SOURCE_DIR":             "paths.source_dir",
    "GENERATED_MD_DIR":       "paths.generated_md_dir",
    "DOC_FILES":              "paths.doc_files",
    "CODE_BASE_PATH":         "paths.code_base_path",
    "CHAT_PROMPT_FILE_PATH":  "paths.chat_prompt_file_path",
    "PROMPT_FILE_PATH":       "paths.prompt_file_path",
    # LLM / API
    "QGENIE_API_KEY":         "llm.qgenie_api_key",
    "ANTHROPIC_API_KEY":      "llm.anthropic_api_key",
    "LLM_MODEL":              "llm.model",
    "STREAMLIT_MODEL":        "llm.streamlit_model",
    "CODING_LLM_MODEL":       "llm.coding_model",
    "EMBEDDING_MODEL":        "embeddings.model",
    "LLM_PROVIDER":           "llm.provider",
    "LLM_MAX_TOKENS":         "llm.max_tokens",
    "LLM_TEMPERATURE":        "llm.temperature",
    "LLM_TIMEOUT":            "llm.timeout",
    # --- ADDED: Chat Endpoint Mapping ---
    "QGENIE_CHAT_ENDPOINT":   "llm.chat_endpoint",
    
    # Logging / feature flags
    "LOG_LEVEL":              "logging.level",
    "EMAIL_ID":               "email.recipients",
    "FORMAT":                 "document.format",
    "TOC":                    "document.toc",
    "HTML":                   "document.html",
    "KEEP_IMG_DIMS":          "document.keep_img_dims",
    "RECALC_IMG_DIMS":        "document.recalc_img_dims",
    "RECALC_MAX_DIMS":        "document.recalc_max_dims",
    "VERBOSE":                "logging.verbose",
    "DEBUG":                  "logging.debug",
    # Postgres
    "POSTGRES_CONNECTION":          "database.connection",
    "POSTGRES_ADMIN_USERNAME":      "database.admin_username",
    "POSTGRES_ADMIN_PASSWORD":      "database.admin_password",
    "POSTGRES_COLLECTION":          "database.collection",
    "POSTGRES_COLLECTION_TABLENAME":"database.collection_tablename",
    "POSTGRES_EMBEDDING_TABLENAME": "database.embedding_tablename",
    "POSTGRES_STORE_NAME":          "database.store_name",
    "POSTGRES_USERNAME":            "database.username",
    "POSTGRES_PASSWORD":            "database.password",
    "POSTGRES_HOST":                "database.host",
    "POSTGRES_PORT":                "database.port",
    "POSTGRES_DATABASE":            "database.database",
    "VECTOR_DATABASE":              "database.vector_database",
    # Mermaid / PDF / Tools
    "WITH_PANDOC":            "tools.pandoc_path",
    "WITH_WMF2SVG":           "tools.wmf2svg_path",
    "MMDC_PATH":              "tools.mmdc_path",
    # Excel colors
    "PASS_COLOR":             "excel.pass_color",
    "FAIL_COLOR":             "excel.fail_color",
    "ALT_ROW_COLOR":          "excel.alt_row_color",
    # SMTP
    "SMTP_HOST":              "email.smtp_host",
    "SMTP_PORT":              "email.smtp_port",
    "SMTP_USERNAME":          "email.smtp_username",
    "SMTP_PASSWORD":          "email.smtp_password",
    "SMTP_USE_TLS":           "email.smtp_use_tls",
    "SMTP_SENDER_EMAIL":      "email.sender_email",
    "SMTP_SENDER_NAME":       "email.sender_name",
}

# Environment variable interpolation pattern: ${VAR} or ${VAR:-default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")

# Path-type keys for automatic resolution (dot-path prefixes)
PATH_KEYS: Set[str] = {
    "paths.source_dir", "paths.code_base_path", "paths.word_doc_folder",
    "paths.out_dir", "paths.generated_md_dir", "paths.flat_json_path",
    "paths.report_json_path", "paths.graph_path", "paths.pdf_path",
    "paths.img_dir", "paths.prompt_file_path", "paths.chat_prompt_file_path",
    "paths.input_path", "paths.output_path",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Base error for configuration operations."""


class ConfigFileError(ConfigError):
    """Configuration file could not be loaded or parsed."""


class ConfigValidationError(ConfigError):
    """Required configuration keys are missing."""


# ---------------------------------------------------------------------------
# YAML Loading Helpers
# ---------------------------------------------------------------------------

def _load_yaml(filepath: str) -> Dict[str, Any]:
    """
    Load a YAML file. Uses PyYAML if available, falls back to a basic parser.

    Args:
        filepath: Path to the YAML file.

    Returns:
        Parsed dict.

    Raises:
        ConfigFileError: If file cannot be read or parsed.
    """
    try:
        import yaml
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except ImportError:
        logger.info("PyYAML not installed; using basic YAML parser.")
        return _basic_yaml_parse(filepath)
    except Exception as e:
        raise ConfigFileError(f"Failed to load YAML config '{filepath}': {e}") from e


def _basic_yaml_parse(filepath: str) -> Dict[str, Any]:
    """
    Minimal YAML parser for simple key: value files (no advanced YAML features).
    Handles one level of nesting, comments, strings, numbers, booleans, lists.

    This is a best-effort fallback; install PyYAML for full support.
    """
    result: Dict[str, Any] = {}
    current_section: Optional[str] = None
    current_list_key: Optional[str] = None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip()

                # Skip empty lines and comments
                if not stripped or stripped.lstrip().startswith("#"):
                    current_list_key = None
                    continue

                # Detect indentation
                indent = len(line) - len(line.lstrip())

                # List item continuation
                if current_list_key and indent >= 4 and stripped.lstrip().startswith("- "):
                    item = stripped.lstrip()[2:].strip().strip('"').strip("'")
                    if current_section:
                        result.setdefault(current_section, {})[current_list_key].append(item)
                    continue
                else:
                    current_list_key = None

                # Top-level section header (no indent, ends with :, no value)
                if indent == 0 and ":" in stripped:
                    key, _, val = stripped.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if not val:
                        current_section = key
                        result.setdefault(current_section, {})
                        continue
                    else:
                        # Top-level scalar
                        result[key] = _coerce_value(val)
                        current_section = None
                        continue

                # Nested key under current section
                if current_section and indent >= 2 and ":" in stripped:
                    key, _, val = stripped.strip().partition(":")
                    key = key.strip()
                    val = val.strip()
                    if not val:
                        # Start of a list
                        result[current_section][key] = []
                        current_list_key = key
                    else:
                        result[current_section][key] = _coerce_value(val)

    except Exception as e:
        raise ConfigFileError(f"Failed to parse YAML '{filepath}': {e}") from e

    return result


def _coerce_value(val: str) -> Any:
    """Coerce a string value to its appropriate Python type."""
    if not val or val == "null":
        return None

    # Strip quotes
    if (val.startswith('"') and val.endswith('"')) or \
       (val.startswith("'") and val.endswith("'")):
        return val[1:-1]

    # Strip inline comments
    if "  #" in val:
        val = val[:val.index("  #")].strip()

    # Booleans
    lower = val.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False

    # Numbers
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        pass

    return val


def _interpolate_env_vars(value: Any) -> Any:
    """
    Recursively interpolate ${ENV_VAR} and ${ENV_VAR:-default} in string values.

    Args:
        value: A string, dict, list, or scalar.

    Returns:
        Value with environment variables resolved.
    """
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            default = match.group(2)  # May be None
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            return match.group(0)  # Keep original if not found
        return _ENV_VAR_PATTERN.sub(_replace, value)
    elif isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_interpolate_env_vars(item) for item in value]
    return value


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """
    Deep-merge two dicts. Values in `override` take precedence.
    Nested dicts are merged recursively; other types are replaced.
    """
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


# ---------------------------------------------------------------------------
# Dot-path accessor
# ---------------------------------------------------------------------------

def _get_by_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    """
    Retrieve a value from a nested dict using dot notation.

    Args:
        data: The nested configuration dict.
        path: Dot-separated key path (e.g., "database.port").
        default: Value to return if path not found.

    Returns:
        The value at the path, or default.
    """
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def _set_by_path(data: Dict[str, Any], path: str, value: Any) -> None:
    """Set a value in a nested dict using dot notation."""
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


# ---------------------------------------------------------------------------
# GlobalConfig
# ---------------------------------------------------------------------------

class GlobalConfig:
    """
    Hierarchical YAML configuration with dot-path access and env-var interpolation.

    Features:
    - Structured sections (paths, llm, database, logging, email, etc.)
    - Dot-path access: config.get("llm.model"), config.get("database.port")
    - Flat-key backward compatibility: config.get("LLM_MODEL") -> llm.model
    - ${ENV_VAR} interpolation in YAML values
    - Environment variable overrides (env vars always win)
    - Type-safe accessors: get_bool, get_int, get_float, get_list, get_path
    - Multiple file layering: base config + environment-specific overrides
    - Required-key validation
    - Section extraction: config.get_section("database")
    - Full interoperability with EnvConfig via to_env_dict()

    Usage:
        config = GlobalConfig()
        config = GlobalConfig(config_file="production.yaml")
        config = GlobalConfig(config_file="base.yaml", override_file="local.yaml")

        model = config.get("llm.model")
        port  = config.get_int("database.port", 5432)
        db    = config.get_section("database")
    """

    # Default search paths for auto-discovery
    SEARCH_PATHS = [
        "global_config.yaml",
        "config/global_config.yaml",
        "../global_config.yaml",
        "global_config.yml",
    ]

    def __init__(
        self,
        config_file: Optional[str] = None,
        override_file: Optional[str] = None,
        required: Optional[List[str]] = None,
        auto_load: bool = True,
        env_override: bool = True,
    ):
        """
        Initialize the global configuration.

        Args:
            config_file: Path to the YAML config file. Auto-discovers if None.
            override_file: Optional second YAML file to layer on top.
            required: List of required dot-paths (e.g., ["llm.api_key"]).
            auto_load: Load configuration immediately on construction.
            env_override: Allow environment variables to override YAML values.
        """
        self._data: Dict[str, Any] = {}
        self._config_file: Optional[str] = None
        self._override_file: Optional[str] = None
        self._required = required or []
        self._env_override = env_override

        if auto_load:
            self.load(config_file, override_file)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(
        self,
        config_file: Optional[str] = None,
        override_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Load, merge, interpolate, and validate configuration.

        Args:
            config_file: Primary YAML file path.
            override_file: Optional override YAML file path.

        Returns:
            The fully resolved configuration dict.
        """
        # Step 1: Find and load base config
        base_path = config_file or self._discover_config_file()
        if base_path and os.path.isfile(base_path):
            self._data = _load_yaml(base_path)
            self._config_file = base_path
            logger.info("Loaded config from: %s", base_path)
        else:
            logger.info("No YAML config file found; using defaults and environment.")
            self._data = {}

        # Step 2: Layer override file
        if override_file and os.path.isfile(override_file):
            override_data = _load_yaml(override_file)
            self._data = _deep_merge(self._data, override_data)
            self._override_file = override_file
            logger.info("Applied override config: %s", override_file)

        # Step 3: Interpolate ${ENV_VAR} references
        self._data = _interpolate_env_vars(self._data)

        # Step 4: Apply environment variable overrides
        if self._env_override:
            self._apply_env_overrides()

        # Step 5: Normalize paths
        self._normalize_paths()

        # Step 6: Validate required keys
        self._validate()

        return self._data

    def _discover_config_file(self) -> Optional[str]:
        """Search for a config file in standard locations."""
        # Check relative to CWD
        for candidate in self.SEARCH_PATHS:
            if os.path.isfile(candidate):
                return candidate

        # Check relative to project root (3 levels up from this file)
        project_root = Path(__file__).resolve().parent.parent.parent
        for candidate in self.SEARCH_PATHS:
            full = project_root / candidate
            if full.is_file():
                return str(full)

        return None

    def _apply_env_overrides(self) -> None:
        """
        Apply environment variable overrides to the loaded configuration.
        Environment variables always win over YAML file values.
        Uses the FLAT_KEY_MAP to map env var names to dot-paths.
        """
        for env_key, dot_path in FLAT_KEY_MAP.items():
            env_val = os.environ.get(env_key)
            if env_val is not None:
                _set_by_path(self._data, dot_path, env_val)
                logger.debug("Env override: %s -> %s", env_key, dot_path)

    def _normalize_paths(self) -> None:
        """Resolve and expand path-type values."""
        for path_key in PATH_KEYS:
            val = _get_by_path(self._data, path_key)
            if val and isinstance(val, str):
                try:
                    resolved = str(Path(val).expanduser().resolve())
                    _set_by_path(self._data, path_key, resolved)
                except Exception:
                    pass

    def _validate(self) -> None:
        """Validate required keys are present and non-empty."""
        missing = []
        for key in self._required:
            val = self.get(key)
            if val is None or val == "":
                missing.append(key)
        if missing:
            msg = f"Missing required configuration keys: {missing}"
            logger.error(msg)
            raise ConfigValidationError(msg)

    # ------------------------------------------------------------------
    # Accessors — Dot-path with flat-key fallback
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by dot-path or flat env-style key.

        Supports both:
            config.get("llm.model")        # dot-path
            config.get("LLM_MODEL")        # flat key (auto-mapped)

        Args:
            key: Dot-path (e.g., "database.port") or flat key (e.g., "POSTGRES_PORT").
            default: Fallback value if key is not found.

        Returns:
            The configuration value, or default.
        """
        # Check if it's a flat key that needs mapping
        if key in FLAT_KEY_MAP:
            dot_path = FLAT_KEY_MAP[key]
            val = _get_by_path(self._data, dot_path)
            if val is not None:
                return val

        # Try direct dot-path lookup
        val = _get_by_path(self._data, key)
        if val is not None:
            return val

        return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean configuration value."""
        val = self.get(key)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on")
        return bool(val)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer configuration value."""
        val = self.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get a float configuration value."""
        val = self.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_list(
        self,
        key: str,
        separator: str = ",",
        default: Optional[List] = None,
    ) -> List:
        """
        Get a list value. Handles YAML native lists and comma-separated strings.

        Args:
            key: Configuration key.
            separator: Separator for string-to-list conversion.
            default: Fallback if key not found.

        Returns:
            List of values.
        """
        val = self.get(key)
        if val is None:
            return default or []
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [item.strip() for item in val.split(separator) if item.strip()]
        return [val]

    def get_path(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a path value, resolved and expanded."""
        val = self.get(key) or default
        if val and isinstance(val, str):
            return str(Path(val).expanduser().resolve())
        return None

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get an entire configuration section as a dict.

        Args:
            section: Top-level section name (e.g., "database", "llm", "paths").

        Returns:
            Dict of key-value pairs for that section, or empty dict.
        """
        val = self._data.get(section)
        if isinstance(val, dict):
            return dict(val)
        return {}

    def has(self, key: str) -> bool:
        """Check if a key is set and non-empty."""
        val = self.get(key)
        return val is not None and val != ""

    # ------------------------------------------------------------------
    # Interoperability
    # ------------------------------------------------------------------

    def to_flat_dict(self) -> Dict[str, Any]:
        """
        Export configuration as a flat dict using ENV-style keys.
        Useful for backward compatibility with code expecting EnvConfig.to_dict().

        Returns:
            Dict mapping flat keys (e.g., "POSTGRES_PORT") to values.
        """
        flat = {}
        for env_key, dot_path in FLAT_KEY_MAP.items():
            val = _get_by_path(self._data, dot_path)
            if val is not None:
                flat[env_key] = val
        return flat

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the full hierarchical configuration."""
        import copy
        return copy.deepcopy(self._data)

    def sections(self) -> List[str]:
        """Return the names of all top-level configuration sections."""
        return [k for k, v in self._data.items() if isinstance(v, dict)]

    # ------------------------------------------------------------------
    # File Generation
    # ------------------------------------------------------------------

    def save(self, filepath: str) -> None:
        """
        Save the current configuration to a YAML file.

        Args:
            filepath: Output file path.

        Raises:
            ConfigError: If PyYAML is not installed.
        """
        try:
            import yaml
        except ImportError:
            raise ConfigError(
                "PyYAML is required to save config. Install with: pip install pyyaml"
            )

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._data,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        logger.info("Config saved to: %s", filepath)

    @classmethod
    def from_env_config(cls, env_config) -> "GlobalConfig":
        """
        Create a GlobalConfig from an existing EnvConfig instance.
        Useful for migrating from .env to YAML.

        Args:
            env_config: An EnvConfig instance.

        Returns:
            A new GlobalConfig populated from the EnvConfig values.
        """
        instance = cls(auto_load=False)

        # Build hierarchical structure from flat EnvConfig
        flat = env_config.to_dict() if hasattr(env_config, "to_dict") else {}
        for env_key, dot_path in FLAT_KEY_MAP.items():
            if env_key in flat and flat[env_key] is not None:
                _set_by_path(instance._data, dot_path, flat[env_key])

        return instance

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        source = self._config_file or "no file"
        override = f" + {self._override_file}" if self._override_file else ""
        sections = self.sections()
        return f"GlobalConfig(source='{source}{override}', sections={sections})"

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(f"Configuration key not found: {key}")
        return val