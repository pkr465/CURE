"""
dependency_builder - CCLS-based C/C++ dependency analysis toolkit.

Provides dependency extraction, call graph traversal, and code navigation
using the ccls Language Server Protocol implementation.

Architecture:
    ┌─────────────────────────────────────────────────┐
    │                DependencyService                │  ← Public API
    │  (validation, health checks, orchestration)     │
    ├─────────────────────────────────────────────────┤
    │             DependencyFetcher                   │  ← Caching & dispatch
    │  (smart cache, endpoint routing)                │
    ├─────────────────────────────────────────────────┤
    │           CCLSDependencyBuilder                 │  ← Dependency logic
    │  (BFS traversal, call graphs, symbol lookup)    │
    ├─────────────────────────────────────────────────┤
    │            CCLSCodeNavigator                    │  ← LSP wrapper
    │  (ccls process, LSP protocol, tokenization)     │
    ├─────────────────────────────────────────────────┤
    │              CCLSIngestion                      │  ← Indexing
    │  (project config, ccls --index)                 │
    └─────────────────────────────────────────────────┘

Supporting modules:
    config.py           - DependencyBuilderConfig dataclass
    exceptions.py       - Custom exception hierarchy
    models.py           - Structured request/response models
    utils.py            - Shared utilities (URI cleaning, path resolution)
    connection_pool.py  - CCLS process pool for reuse
    metrics.py          - Observability and performance tracking
"""

# --- Core Public API ---
from dependency_builder.dependency_service import DependencyService
from dependency_builder.ccls_ingestion import CCLSIngestion
from dependency_builder.dependency_handler import CacheMetadata, DependencyFetcher
from dependency_builder.ccls_dependency_builder import CCLSDependencyBuilder
from dependency_builder.ccls_code_navigator import CCLSCodeNavigator

# --- Configuration ---
from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG

# --- Models ---
from dependency_builder.models import (
    FetchRequest,
    FetchResponse,
    EndpointType,
    HealthStatus,
    SymbolKind,
)

# --- Exceptions ---
from dependency_builder.exceptions import (
    DependencyBuilderError,
    CCLSError,
    CCLSNotFoundError,
    CCLSVersionError,
    CCLSStartupError,
    IndexingError,
    IndexingFailedError,
    IndexingTimeoutError,
    IndexNotFoundError,
    LSPError,
    CacheError,
    ValidationError,
    PoolError,
)

# --- Infrastructure ---
from dependency_builder.connection_pool import CCLSConnectionPool
from dependency_builder.metrics import MetricsCollector, get_metrics

# --- Utilities ---
from dependency_builder.utils import clean_uri, to_uri, resolve_file_path

__version__ = "2.0.0"

__all__ = [
    # Core
    "DependencyService",
    "CCLSIngestion",
    "CacheMetadata",
    "DependencyFetcher",
    "CCLSDependencyBuilder",
    "CCLSCodeNavigator",
    # Config
    "DependencyBuilderConfig",
    "DEFAULT_CONFIG",
    # Models
    "FetchRequest",
    "FetchResponse",
    "EndpointType",
    "HealthStatus",
    "SymbolKind",
    # Exceptions
    "DependencyBuilderError",
    "CCLSError",
    "CCLSNotFoundError",
    "CCLSVersionError",
    "CCLSStartupError",
    "IndexingError",
    "IndexingFailedError",
    "IndexingTimeoutError",
    "IndexNotFoundError",
    "LSPError",
    "CacheError",
    "ValidationError",
    "PoolError",
    # Infrastructure
    "CCLSConnectionPool",
    "MetricsCollector",
    "get_metrics",
    # Utilities
    "clean_uri",
    "to_uri",
    "resolve_file_path",
]
