"""
Custom exception hierarchy for the dependency_builder package.

Provides structured, typed exceptions instead of generic string error messages,
enabling callers to handle specific failure modes gracefully.
"""


class DependencyBuilderError(Exception):
    """Base exception for all dependency_builder errors."""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


# --- CCLS Process Errors ---

class CCLSError(DependencyBuilderError):
    """Base exception for CCLS-related errors."""
    pass


class CCLSNotFoundError(CCLSError):
    """CCLS executable not found in PATH."""

    def __init__(self, executable: str = "ccls"):
        super().__init__(
            f"CCLS executable '{executable}' not found in PATH. "
            f"Install ccls: sudo apt install ccls (Linux) or brew install ccls (macOS)",
            details={"executable": executable}
        )


class CCLSVersionError(CCLSError):
    """CCLS version does not meet minimum requirements."""

    def __init__(self, found_version: str, min_version: tuple):
        super().__init__(
            f"CCLS version {found_version} is below minimum {min_version[0]}.{min_version[1]}",
            details={"found_version": found_version, "min_version": min_version}
        )


class CCLSStartupError(CCLSError):
    """CCLS process failed to start."""

    def __init__(self, reason: str = "Unknown error"):
        super().__init__(
            f"Failed to start ccls process: {reason}",
            details={"reason": reason}
        )


class CCLSProcessDiedError(CCLSError):
    """CCLS process terminated unexpectedly."""

    def __init__(self, exit_code: int = None):
        msg = "CCLS process terminated unexpectedly"
        if exit_code is not None:
            msg += f" with exit code {exit_code}"
        super().__init__(msg, details={"exit_code": exit_code})


# --- Indexing Errors ---

class IndexingError(DependencyBuilderError):
    """Base exception for indexing-related errors."""
    pass


class IndexingFailedError(IndexingError):
    """CCLS indexing failed for a project."""

    def __init__(self, project_root: str, reason: str = "Unknown"):
        super().__init__(
            f"CCLS indexing failed for '{project_root}': {reason}",
            details={"project_root": project_root, "reason": reason}
        )


class IndexingTimeoutError(IndexingError):
    """CCLS indexing timed out."""

    def __init__(self, project_root: str, timeout_seconds: int):
        super().__init__(
            f"CCLS indexing timed out after {timeout_seconds}s for '{project_root}'",
            details={"project_root": project_root, "timeout_seconds": timeout_seconds}
        )


class IndexNotFoundError(IndexingError):
    """CCLS index cache not found for a project."""

    def __init__(self, cache_path: str):
        super().__init__(
            f"CCLS index cache not found at '{cache_path}'. Run ingestion first.",
            details={"cache_path": cache_path}
        )


# --- LSP Errors ---

class LSPError(DependencyBuilderError):
    """Base exception for LSP protocol errors."""
    pass


class LSPTimeoutError(LSPError):
    """LSP request timed out."""

    def __init__(self, method: str, timeout: int):
        super().__init__(
            f"LSP request '{method}' timed out after {timeout}s",
            details={"method": method, "timeout": timeout}
        )


class LSPInitializationError(LSPError):
    """LSP session failed to initialize."""

    def __init__(self, reason: str = "Unknown"):
        super().__init__(
            f"LSP session initialization failed: {reason}",
            details={"reason": reason}
        )


# --- File Errors ---

class FileError(DependencyBuilderError):
    """Base exception for file-related errors."""
    pass


class FileNotFoundInProjectError(FileError):
    """Requested file not found in the project."""

    def __init__(self, file_path: str, project_root: str):
        super().__init__(
            f"File '{file_path}' not found in project '{project_root}'",
            details={"file_path": file_path, "project_root": project_root}
        )


class LibclangNotFoundError(FileError):
    """libclang shared library not found."""

    def __init__(self, searched_paths: list = None):
        super().__init__(
            "Could not find libclang.so. Tokenization features will be unavailable.",
            details={"searched_paths": searched_paths or []}
        )


# --- Cache Errors ---

class CacheError(DependencyBuilderError):
    """Base exception for cache-related errors."""
    pass


class CacheCorruptedError(CacheError):
    """Cache metadata is corrupted."""

    def __init__(self, cache_path: str, reason: str = ""):
        super().__init__(
            f"Cache metadata corrupted at '{cache_path}': {reason}",
            details={"cache_path": cache_path, "reason": reason}
        )


# --- Validation Errors ---

class ValidationError(DependencyBuilderError):
    """Input validation failed."""

    def __init__(self, field: str, message: str):
        super().__init__(
            f"Validation error for '{field}': {message}",
            details={"field": field, "message": message}
        )


class InvalidEndpointError(ValidationError):
    """Unknown endpoint type requested."""

    def __init__(self, endpoint: str, valid_endpoints: frozenset):
        super().__init__(
            field="endpoint_type",
            message=f"Unknown endpoint '{endpoint}'. Valid: {sorted(valid_endpoints)}"
        )
        self.details["endpoint"] = endpoint
        self.details["valid_endpoints"] = sorted(valid_endpoints)


# --- Connection Pool Errors ---

class PoolError(DependencyBuilderError):
    """Base exception for connection pool errors."""
    pass


class PoolExhaustedError(PoolError):
    """No available connections in the pool."""

    def __init__(self, pool_size: int):
        super().__init__(
            f"Connection pool exhausted (max_size={pool_size}). "
            f"All connections are in use. Consider increasing pool_max_size.",
            details={"pool_size": pool_size}
        )


class PoolConnectionError(PoolError):
    """Failed to acquire a connection from the pool."""

    def __init__(self, reason: str = "Unknown"):
        super().__init__(
            f"Failed to acquire pool connection: {reason}",
            details={"reason": reason}
        )
