"""
Structured data models for the dependency_builder package.

Defines typed request/response objects using dataclasses instead of
implicit Dict[str, Any] types, providing compile-time documentation,
IDE autocompletion, and runtime validation.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
from enum import Enum


# --- Enums ---

class EndpointType(str, Enum):
    """Supported dependency fetch endpoint types."""
    HEALTH_CHECK = "health_check"
    FETCH_BY_COMPONENT = "fetch_dependencies_by_component"
    FETCH_BY_LINE_CHARACTER = "fetch_dependencies_by_line_character"
    FETCH_BY_FILE = "fetch_dependencies_by_file"

    @classmethod
    def from_string(cls, value: str) -> Optional["EndpointType"]:
        """Resolve endpoint type from a string, supporting partial matches."""
        cleaned = value.strip()
        for member in cls:
            if member.value == cleaned or cleaned in member.value:
                return member
        return None


class SymbolKind(Enum):
    """LSP Symbol Kinds (standard + ccls extensions)."""
    FILE = 1
    MODULE = 2
    NAMESPACE = 3
    PACKAGE = 4
    CLASS = 5
    METHOD = 6
    PROPERTY = 7
    FIELD = 8
    CONSTRUCTOR = 9
    ENUM = 10
    INTERFACE = 11
    FUNCTION = 12
    VARIABLE = 13
    CONSTANT = 14
    STRING = 15
    NUMBER = 16
    BOOLEAN = 17
    ARRAY = 18
    OBJECT = 19
    KEY = 20
    NULL = 21
    ENUM_MEMBER = 22
    STRUCT = 23
    EVENT = 24
    OPERATOR = 25
    TYPE_PARAMETER = 26
    UNKNOWN = 255

    @classmethod
    def from_id(cls, kind_id: int) -> "SymbolKind":
        """Get SymbolKind from LSP kind ID."""
        try:
            return cls(kind_id)
        except ValueError:
            return cls.UNKNOWN

    @property
    def display_name(self) -> str:
        """Human-readable name for the symbol kind."""
        return self.name.replace("_", " ").title()


# --- Request Models ---

@dataclass
class FetchRequest:
    """
    Structured request for a dependency fetch operation.
    Replaces the previous untyped keyword arguments to process_message().
    """
    project_root: str
    output_dir: str
    codebase_identifier: str
    endpoint_type: str
    file_name: str = ""
    function_name: Optional[str] = None
    level: int = 1
    line: Optional[int] = None
    character: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None

    def __post_init__(self):
        """Normalize types after initialization."""
        if self.level is not None:
            try:
                self.level = int(self.level)
            except (ValueError, TypeError):
                self.level = 1

        if self.line is not None:
            try:
                self.line = int(self.line)
            except (ValueError, TypeError):
                self.line = None

        if self.character is not None:
            try:
                self.character = int(self.character)
            except (ValueError, TypeError):
                self.character = None

        if self.start is not None:
            try:
                self.start = int(self.start)
            except (ValueError, TypeError):
                self.start = None

        if self.end is not None:
            try:
                self.end = int(self.end)
            except (ValueError, TypeError):
                self.end = None

    @property
    def resolved_endpoint(self) -> Optional[EndpointType]:
        """Resolve the endpoint_type string to an EndpointType enum."""
        return EndpointType.from_string(self.endpoint_type)

    def validate(self) -> List[str]:
        """
        Validate request fields and return a list of error messages.
        Returns empty list if valid.
        """
        errors = []

        if not self.project_root:
            errors.append("project_root is required")

        if not self.output_dir:
            errors.append("output_dir is required")

        if not self.endpoint_type or not self.endpoint_type.strip():
            errors.append("endpoint_type is required")

        resolved = self.resolved_endpoint
        if resolved is None:
            errors.append(
                f"Unknown endpoint_type '{self.endpoint_type}'. "
                f"Valid: {[e.value for e in EndpointType]}"
            )

        # File name required for non-health-check endpoints
        if resolved and resolved != EndpointType.HEALTH_CHECK:
            if not self.file_name:
                errors.append("file_name is required for fetch endpoints")

            if resolved == EndpointType.FETCH_BY_COMPONENT and not self.function_name:
                errors.append("function_name is required for fetch_by_component")

            if resolved == EndpointType.FETCH_BY_LINE_CHARACTER:
                if self.line is None:
                    errors.append("line is required for fetch_by_line_character")
                if self.character is None:
                    errors.append("character is required for fetch_by_line_character")

        return errors


# --- Response Models ---

@dataclass
class FetchResponse:
    """
    Structured response from a dependency fetch operation.
    Replaces the previous Dict[str, Any] return values.
    """
    message: str
    data: Any = field(default_factory=dict)
    cached: bool = False
    error: bool = False
    error_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to the legacy dict format for backward compatibility."""
        result = {
            "message": self.message,
            "data": self.data,
        }
        if self.cached:
            result["cached"] = True
        if self.error:
            result["error"] = True
            if self.error_type:
                result["error_type"] = self.error_type
        return result

    @classmethod
    def success(cls, data: Any, message: str = "success", cached: bool = False) -> "FetchResponse":
        """Create a success response."""
        return cls(message=message, data=data, cached=cached)

    @classmethod
    def error_response(cls, message: str, error_type: str = "general") -> "FetchResponse":
        """Create an error response."""
        return cls(message=message, data={}, error=True, error_type=error_type)

    @classmethod
    def health_ok(cls) -> "FetchResponse":
        """Create a health check success response."""
        return cls(message="RUNNING OK", data={})


# --- Dependency Data Models ---

@dataclass
class SourceLocation:
    """A location in source code."""
    uri: str = ""
    file_path: str = ""
    line: int = 0
    character: int = 0
    end_line: Optional[int] = None
    end_character: Optional[int] = None

    def to_lsp_range(self) -> Dict[str, Any]:
        """Convert to LSP Range format."""
        return {
            "start": {"line": self.line, "character": self.character},
            "end": {
                "line": self.end_line if self.end_line is not None else self.line,
                "character": self.end_character if self.end_character is not None else self.character,
            },
        }


@dataclass
class SymbolInfo:
    """Information about a code symbol (function, variable, struct, etc.)."""
    name: str
    kind: str = "Unknown"
    definition: str = ""
    file_path: str = ""
    uri: str = ""
    location: Optional[SourceLocation] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "name": self.name,
            "kind": self.kind,
            "definition": self.definition,
            "file": self.file_path,
            "uri": self.uri,
        }
        if self.location:
            result["start"] = {
                "line": self.location.line,
                "character": self.location.character,
            }
        return result


@dataclass
class DependencyResult:
    """Result of a dependency analysis for a single symbol."""
    name: str
    file_path: str = ""
    definition: str = ""
    dependencies: Dict[str, Any] = field(default_factory=lambda: {
        "successors": {},
        "predecessors": {},
    })

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "file": self.file_path,
            "definition": self.definition,
            "dependencies": self.dependencies,
        }


@dataclass
class CacheEntry:
    """Represents a single cache entry with source file fingerprint."""
    cache_key: str
    artifact_path: str
    source_file: str
    created_at: float = 0.0
    source_fingerprint: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "artifact_path": self.artifact_path,
            "source_file": self.source_file,
            "created_at": self.created_at,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_dict(cls, cache_key: str, data: Dict[str, Any]) -> "CacheEntry":
        """Deserialize from JSON dict."""
        return cls(
            cache_key=cache_key,
            artifact_path=data.get("artifact_path", ""),
            source_file=data.get("source_file", ""),
            created_at=data.get("created_at", 0.0),
            source_fingerprint=data.get("source_fingerprint", {}),
        )


@dataclass
class HealthStatus:
    """Health check status for the dependency builder system."""
    ccls_available: bool = False
    ccls_version: str = ""
    index_exists: bool = False
    cache_writable: bool = False
    libclang_loaded: bool = False
    stale_cache_entries: int = 0
    total_cache_entries: int = 0

    @property
    def is_healthy(self) -> bool:
        """Overall health status."""
        return self.ccls_available and self.index_exists

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "healthy": self.is_healthy,
            "ccls_available": self.ccls_available,
            "ccls_version": self.ccls_version,
            "index_exists": self.index_exists,
            "cache_writable": self.cache_writable,
            "libclang_loaded": self.libclang_loaded,
            "stale_cache_entries": self.stale_cache_entries,
            "total_cache_entries": self.total_cache_entries,
        }
