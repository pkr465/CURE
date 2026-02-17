"""
Centralized configuration for the dependency_builder package.

All tunable constants, timeouts, limits, and paths are defined here
as a single dataclass to avoid scattering magic numbers across modules.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DependencyBuilderConfig:
    """
    Configuration object for the entire dependency_builder pipeline.
    Instantiate with defaults or override specific values.

    Example:
        config = DependencyBuilderConfig(indexing_timeout_seconds=600)
        config = DependencyBuilderConfig.from_env()
    """

    # --- CCLS Process ---
    ccls_executable: str = "ccls"
    ccls_log_file: str = "ccls.log"
    ccls_verbosity: int = 1
    ccls_process_startup_delay: float = 0.5

    # --- LSP Settings ---
    lsp_endpoint_timeout: int = 30
    lsp_initialization_delay: float = 0.5
    index_threads: Optional[int] = None  # None = use os.cpu_count()

    # --- Indexing ---
    min_ccls_version: tuple = (0, 20210330)
    version_check_timeout: int = 10
    indexing_timeout_seconds: int = 1200  # 20 minutes
    log_output_truncation: int = 500
    log_error_truncation: int = 1000

    # --- BFS Traversal ---
    max_bfs_depth: int = 10
    max_nodes_per_level: int = 200
    default_callee_level: int = 5
    default_caller_level: int = 5

    # --- File Caching ---
    file_cache_maxsize: int = 256
    cache_metadata_filename: str = ".cache_metadata.json"
    hash_chunk_size: int = 8192

    # --- Filename Safety ---
    max_filename_length: int = 200
    hash_suffix_length: int = 12
    filename_prefix_length: int = 180

    # --- Recursion Limits ---
    max_reference_depth: int = 10
    max_call_flow_depth: int = 1

    # --- Libclang ---
    libclang_search_paths: List[str] = field(default_factory=lambda: [
        "/usr/lib/llvm-14/lib/libclang.so",
        "/usr/lib/llvm-15/lib/libclang.so",
        "/usr/lib/llvm-16/lib/libclang.so",
        "/usr/lib/x86_64-linux-gnu/libclang.so",
        "/usr/local/lib/libclang.so",
        "/Library/Developer/CommandLineTools/usr/lib/libclang.dylib",
    ])
    virtual_snippet_filename: str = "snippet.cpp"

    # --- CCLS Config Defaults ---
    default_c_standard: str = "c11"
    default_cpp_standard: str = "c++17"
    ccls_ignore_patterns: List[str] = field(default_factory=lambda: [
        r".*\.o$", r".*\.d$", r".*\.ko$",
        r".*\.mod\.c", r".*\.cmd",
        r".*\.txt", r".*\.log", r".*\.bin",
    ])

    # --- Connection Pool ---
    pool_max_size: int = 3
    pool_idle_timeout: float = 300.0  # 5 minutes
    pool_health_check_interval: float = 60.0  # 1 minute

    # --- Process Cleanup ---
    sigterm_timeout: int = 3
    sigkill_timeout: int = 2

    # --- Valid Endpoints ---
    valid_endpoints: frozenset = field(default_factory=lambda: frozenset({
        "health_check",
        "fetch_dependencies_by_component",
        "fetch_dependencies_by_line_character",
        "fetch_dependencies_by_file",
    }))

    @property
    def effective_index_threads(self) -> int:
        """Returns the number of indexing threads to use."""
        return self.index_threads or (os.cpu_count() or 2)

    @classmethod
    def from_env(cls) -> "DependencyBuilderConfig":
        """
        Create a configuration from environment variables.
        Environment variables are prefixed with DEPBUILDER_.
        """
        kwargs = {}

        env_map = {
            "DEPBUILDER_CCLS_BIN": "ccls_executable",
            "DEPBUILDER_LSP_TIMEOUT": ("lsp_endpoint_timeout", int),
            "DEPBUILDER_INDEX_TIMEOUT": ("indexing_timeout_seconds", int),
            "DEPBUILDER_MAX_BFS_DEPTH": ("max_bfs_depth", int),
            "DEPBUILDER_MAX_NODES_PER_LEVEL": ("max_nodes_per_level", int),
            "DEPBUILDER_FILE_CACHE_SIZE": ("file_cache_maxsize", int),
            "DEPBUILDER_POOL_MAX_SIZE": ("pool_max_size", int),
            "DEPBUILDER_POOL_IDLE_TIMEOUT": ("pool_idle_timeout", float),
            "DEPBUILDER_INDEX_THREADS": ("index_threads", int),
            "LIBCLANG_PATH": None,  # Handled separately
        }

        for env_key, field_info in env_map.items():
            val = os.environ.get(env_key)
            if val is None:
                continue

            if field_info is None:
                continue  # Special handling

            if isinstance(field_info, str):
                kwargs[field_info] = val
            elif isinstance(field_info, tuple):
                field_name, converter = field_info
                try:
                    kwargs[field_name] = converter(val)
                except (ValueError, TypeError):
                    pass

        # Handle LIBCLANG_PATH specially
        libclang_path = os.environ.get("LIBCLANG_PATH")
        if libclang_path:
            config = cls(**kwargs)
            config.libclang_search_paths.insert(0, libclang_path)
            return config

        return cls(**kwargs)

    def validate(self) -> List[str]:
        """
        Validate configuration values and return list of warnings.
        Returns empty list if all values are valid.
        """
        warnings = []

        if self.max_bfs_depth < 1:
            warnings.append(f"max_bfs_depth must be >= 1, got {self.max_bfs_depth}")
        if self.max_bfs_depth > 50:
            warnings.append(f"max_bfs_depth={self.max_bfs_depth} is very high, may cause memory issues")

        if self.max_nodes_per_level < 10:
            warnings.append(f"max_nodes_per_level must be >= 10, got {self.max_nodes_per_level}")

        if self.lsp_endpoint_timeout < 5:
            warnings.append(f"lsp_endpoint_timeout={self.lsp_endpoint_timeout}s is very low")

        if self.indexing_timeout_seconds < 60:
            warnings.append(f"indexing_timeout_seconds={self.indexing_timeout_seconds}s may be too low for large projects")

        if self.pool_max_size < 1:
            warnings.append(f"pool_max_size must be >= 1, got {self.pool_max_size}")

        if self.file_cache_maxsize < 16:
            warnings.append(f"file_cache_maxsize={self.file_cache_maxsize} is very low")

        return warnings


# Module-level default configuration instance
DEFAULT_CONFIG = DependencyBuilderConfig()
