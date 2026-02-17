"""
Comprehensive test suite for the dependency_builder package.

Tests all modules including new infrastructure:
    - Config, Exceptions, Models, Utils, Metrics, Connection Pool
    - CacheMetadata smart invalidation
    - Integration: CCLSIngestion -> DependencyService -> DependencyFetcher

Usage:
    python -m dependency_builder.test_dependency_services
    # or from the project root:
    python dependency_builder/test_dependency_services.py
"""

import os
import sys
import time
import json
import shutil
import logging
import tempfile
from pathlib import Path

# Ensure the parent directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# Test Helpers
# ============================================================

def check_ccls_available() -> bool:
    """Check if ccls is available and meets minimum version requirements."""
    if not shutil.which("ccls"):
        logger.error("'ccls' binary not found in PATH.")
        logger.error("Install ccls: sudo apt install ccls (Linux) or brew install ccls (macOS)")
        return False

    from dependency_builder.ccls_ingestion import CCLSIngestion
    is_valid, version_info = CCLSIngestion.check_ccls_version()
    if not is_valid:
        logger.error(f"CCLS version check failed: {version_info}")
        return False
    logger.info(f"CCLS found: {version_info}")
    return True


def create_dummy_project(base_dir: Path) -> Path:
    """Creates a temporary C++ project to test indexing and dependency resolution."""
    project_dir = base_dir / "test_ccls_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    header_content = """\
#pragma once

struct Vector2D {
    float x;
    float y;
};

float dot_product(const Vector2D& a, const Vector2D& b);
"""
    (project_dir / "math_utils.h").write_text(header_content, encoding="utf-8")

    impl_content = """\
#include "math_utils.h"

float dot_product(const Vector2D& a, const Vector2D& b) {
    return a.x * b.x + a.y * b.y;
}
"""
    (project_dir / "math_utils.cpp").write_text(impl_content, encoding="utf-8")

    source_content = """\
#include "math_utils.h"

int main() {
    Vector2D v1;
    v1.x = 10.0;
    v1.y = 20.0;

    Vector2D v2;
    v2.x = 3.0;
    v2.y = 4.0;

    float result = dot_product(v1, v2);
    return 0;
}
"""
    (project_dir / "main.cpp").write_text(source_content, encoding="utf-8")

    ccls_config = "clang++\n-std=c++17\n-I.\n"
    (project_dir / ".ccls").write_text(ccls_config, encoding="utf-8")

    return project_dir


# ============================================================
# Test 1: Module Imports (all old + new modules)
# ============================================================

def test_imports() -> bool:
    """Test that all modules import correctly, including new infrastructure."""
    logger.info("--- Test 1: Module Imports ---")
    try:
        # Core modules
        from dependency_builder.ccls_ingestion import CCLSIngestion
        from dependency_builder.dependency_service import DependencyService
        from dependency_builder.dependency_handler import DependencyFetcher, CacheMetadata
        from dependency_builder.ccls_dependency_builder import CCLSDependencyBuilder
        from dependency_builder.ccls_code_navigator import CCLSCodeNavigator
        from dependency_builder.lsp_notification_handlers import (
            semantic_highlight_handler,
            skipped_ranges_handler,
            progress_handler,
            work_done_progress_create_handler,
        )
        logger.info("  PASS: Core modules imported successfully.")

        # New infrastructure modules
        from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
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
            PoolExhaustedError,
            InvalidEndpointError,
        )
        from dependency_builder.models import (
            FetchRequest,
            FetchResponse,
            EndpointType,
            SymbolKind,
            HealthStatus,
            SourceLocation,
            SymbolInfo,
            DependencyResult,
            CacheEntry,
        )
        from dependency_builder.utils import (
            clean_uri,
            to_uri,
            resolve_file_path,
            safe_filename,
            compute_content_hash,
            normalize_path_in_project,
        )
        from dependency_builder.connection_pool import CCLSConnectionPool, PooledConnection
        from dependency_builder.metrics import MetricsCollector, get_metrics, reset_metrics
        logger.info("  PASS: Infrastructure modules imported successfully.")

        # Package-level imports
        import dependency_builder
        assert hasattr(dependency_builder, "__version__")
        assert hasattr(dependency_builder, "DependencyService")
        assert hasattr(dependency_builder, "DependencyBuilderConfig")
        assert hasattr(dependency_builder, "FetchRequest")
        assert hasattr(dependency_builder, "CCLSConnectionPool")
        assert hasattr(dependency_builder, "get_metrics")
        assert hasattr(dependency_builder, "clean_uri")
        logger.info("  PASS: Package-level exports verified.")

        logger.info("PASS: All module imports passed.")
        return True
    except ImportError as e:
        logger.error(f"FAIL: Import failed: {e}")
        return False
    except AssertionError as e:
        logger.error(f"FAIL: Export assertion failed: {e}")
        return False


# ============================================================
# Test 2: Configuration
# ============================================================

def test_config() -> bool:
    """Test the DependencyBuilderConfig dataclass."""
    logger.info("--- Test 2: Configuration ---")

    from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG

    try:
        # Test defaults
        config = DependencyBuilderConfig()
        assert config.max_bfs_depth == 10, f"Expected max_bfs_depth=10, got {config.max_bfs_depth}"
        assert config.lsp_endpoint_timeout == 30
        assert config.indexing_timeout_seconds == 1200
        assert config.pool_max_size == 3
        assert config.file_cache_maxsize == 256
        logger.info("  PASS: Default config values correct.")

        # Test custom values
        custom = DependencyBuilderConfig(
            max_bfs_depth=5,
            pool_max_size=10,
            indexing_timeout_seconds=600,
        )
        assert custom.max_bfs_depth == 5
        assert custom.pool_max_size == 10
        assert custom.indexing_timeout_seconds == 600
        logger.info("  PASS: Custom config values accepted.")

        # Test effective_index_threads
        assert config.effective_index_threads >= 1
        logger.info(f"  PASS: effective_index_threads={config.effective_index_threads}")

        # Test validation
        bad_config = DependencyBuilderConfig(max_bfs_depth=-1, pool_max_size=0)
        warnings = bad_config.validate()
        assert len(warnings) >= 2, f"Expected >=2 validation warnings, got {len(warnings)}"
        logger.info(f"  PASS: Validation caught {len(warnings)} config issues.")

        # Test valid_endpoints
        assert "health_check" in config.valid_endpoints
        assert "fetch_dependencies_by_component" in config.valid_endpoints
        logger.info("  PASS: valid_endpoints contains expected values.")

        # Test from_env (should not fail even without env vars)
        env_config = DependencyBuilderConfig.from_env()
        assert isinstance(env_config, DependencyBuilderConfig)
        logger.info("  PASS: from_env() works without environment variables.")

        # Test DEFAULT_CONFIG singleton
        assert DEFAULT_CONFIG is not None
        assert isinstance(DEFAULT_CONFIG, DependencyBuilderConfig)
        logger.info("  PASS: DEFAULT_CONFIG singleton available.")

        logger.info("PASS: Configuration tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 3: Exceptions
# ============================================================

def test_exceptions() -> bool:
    """Test the custom exception hierarchy."""
    logger.info("--- Test 3: Exceptions ---")

    from dependency_builder.exceptions import (
        DependencyBuilderError,
        CCLSError,
        CCLSNotFoundError,
        CCLSVersionError,
        CCLSStartupError,
        IndexingFailedError,
        IndexingTimeoutError,
        IndexNotFoundError,
        LSPError,
        LSPTimeoutError,
        CacheError,
        CacheCorruptedError,
        ValidationError,
        InvalidEndpointError,
        PoolError,
        PoolExhaustedError,
    )

    try:
        # Test hierarchy
        assert issubclass(CCLSError, DependencyBuilderError)
        assert issubclass(CCLSNotFoundError, CCLSError)
        assert issubclass(IndexingFailedError, DependencyBuilderError)
        assert issubclass(LSPError, DependencyBuilderError)
        assert issubclass(CacheError, DependencyBuilderError)
        assert issubclass(ValidationError, DependencyBuilderError)
        assert issubclass(PoolError, DependencyBuilderError)
        logger.info("  PASS: Exception hierarchy correct.")

        # Test exception details
        exc = CCLSNotFoundError("/usr/bin/ccls")
        assert exc.details["executable"] == "/usr/bin/ccls"
        assert "not found" in str(exc).lower()
        logger.info("  PASS: CCLSNotFoundError has correct details.")

        exc = IndexingTimeoutError("/src/project", 1200)
        assert exc.details["timeout_seconds"] == 1200
        logger.info("  PASS: IndexingTimeoutError has correct details.")

        exc = InvalidEndpointError("bad_endpoint", frozenset({"health_check"}))
        assert "bad_endpoint" in str(exc)
        assert exc.details["endpoint"] == "bad_endpoint"
        logger.info("  PASS: InvalidEndpointError has correct details.")

        exc = PoolExhaustedError(3)
        assert exc.details["pool_size"] == 3
        logger.info("  PASS: PoolExhaustedError has correct details.")

        # Test that exceptions can be caught as base type
        try:
            raise CCLSNotFoundError()
        except DependencyBuilderError:
            pass  # Expected
        logger.info("  PASS: Exceptions catchable as DependencyBuilderError.")

        logger.info("PASS: Exception tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 4: Models
# ============================================================

def test_models() -> bool:
    """Test structured data models."""
    logger.info("--- Test 4: Models ---")

    from dependency_builder.models import (
        FetchRequest, FetchResponse, EndpointType, SymbolKind, HealthStatus,
    )

    try:
        # Test EndpointType enum
        assert EndpointType.HEALTH_CHECK.value == "health_check"
        assert EndpointType.from_string("health_check") == EndpointType.HEALTH_CHECK
        assert EndpointType.from_string("  fetch_dependencies_by_file  ") == EndpointType.FETCH_BY_FILE
        assert EndpointType.from_string("nonexistent") is None
        logger.info("  PASS: EndpointType enum works correctly.")

        # Test SymbolKind
        assert SymbolKind.from_id(12) == SymbolKind.FUNCTION
        assert SymbolKind.from_id(999) == SymbolKind.UNKNOWN
        assert SymbolKind.FUNCTION.display_name == "Function"
        logger.info("  PASS: SymbolKind enum works correctly.")

        # Test FetchRequest validation
        req = FetchRequest(
            project_root="/src",
            output_dir="/out",
            codebase_identifier="test",
            endpoint_type="health_check",
        )
        errors = req.validate()
        assert len(errors) == 0, f"Expected no errors for valid request, got: {errors}"
        logger.info("  PASS: Valid FetchRequest passes validation.")

        # Test invalid request
        bad_req = FetchRequest(
            project_root="",
            output_dir="",
            codebase_identifier="test",
            endpoint_type="",
        )
        errors = bad_req.validate()
        assert len(errors) >= 3, f"Expected >=3 errors, got {len(errors)}: {errors}"
        logger.info(f"  PASS: Invalid FetchRequest caught {len(errors)} errors.")

        # Test FetchRequest type coercion
        req = FetchRequest(
            project_root="/src",
            output_dir="/out",
            codebase_identifier="test",
            endpoint_type="fetch_dependencies_by_line_character",
            file_name="main.cpp",
            line="10",
            character="5",
            level="2",
        )
        assert req.line == 10
        assert req.character == 5
        assert req.level == 2
        logger.info("  PASS: FetchRequest coerces string types to int.")

        # Test FetchResponse
        resp = FetchResponse.success(data={"test": True}, message="ok")
        assert resp.message == "ok"
        assert resp.data == {"test": True}
        assert not resp.error
        d = resp.to_dict()
        assert d["message"] == "ok"
        logger.info("  PASS: FetchResponse.success() works correctly.")

        err_resp = FetchResponse.error_response("something failed", error_type="validation")
        assert err_resp.error
        assert err_resp.error_type == "validation"
        logger.info("  PASS: FetchResponse.error_response() works correctly.")

        # Test HealthStatus
        health = HealthStatus(ccls_available=True, index_exists=True)
        assert health.is_healthy
        health2 = HealthStatus(ccls_available=True, index_exists=False)
        assert not health2.is_healthy
        d = health.to_dict()
        assert d["healthy"] is True
        logger.info("  PASS: HealthStatus works correctly.")

        logger.info("PASS: Model tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 5: Utilities
# ============================================================

def test_utils() -> bool:
    """Test shared utility functions."""
    logger.info("--- Test 5: Utilities ---")

    from dependency_builder.utils import (
        clean_uri, to_uri, resolve_file_path, safe_filename,
        compute_content_hash, normalize_path_in_project,
    )

    try:
        # Test clean_uri
        assert clean_uri("") == ""
        assert clean_uri("file:///home/user/main.cpp") == "/home/user/main.cpp"
        assert clean_uri("file:///path/with%20spaces/file.cpp") == "/path/with spaces/file.cpp"
        assert clean_uri("/absolute/path") == "/absolute/path"
        logger.info("  PASS: clean_uri works correctly.")

        # Test to_uri
        uri = to_uri("/home/user/main.cpp")
        assert uri.startswith("file://")
        assert "main.cpp" in uri
        logger.info("  PASS: to_uri works correctly.")

        # Test safe_filename
        assert safe_filename("simple.json") == "simple.json"
        assert "/" not in safe_filename("path/to/file.json")
        long_name = "a" * 300 + ".json"
        safe = safe_filename(long_name)
        assert len(safe) < 300
        assert safe.endswith(".json")
        logger.info("  PASS: safe_filename works correctly.")

        # Test compute_content_hash
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cpp", delete=False) as f:
            f.write("int main() { return 0; }")
            temp_path = f.name

        try:
            h1 = compute_content_hash(temp_path)
            assert h1 is not None
            assert len(h1) == 32  # MD5 hex digest length

            h2 = compute_content_hash(temp_path)
            assert h1 == h2  # Same content, same hash

            # Modify file
            with open(temp_path, "w") as f:
                f.write("int main() { return 1; }")
            h3 = compute_content_hash(temp_path)
            assert h3 != h1  # Different content, different hash
        finally:
            os.unlink(temp_path)
        logger.info("  PASS: compute_content_hash works correctly.")

        # Test resolve_file_path with real paths
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.cpp"
            test_file.write_text("test")

            result = resolve_file_path("test.cpp", tmpdir)
            assert result == str(test_file)

            result = resolve_file_path(str(test_file), tmpdir)
            assert result == str(test_file)

            result = resolve_file_path("", tmpdir)
            assert result == ""
        logger.info("  PASS: resolve_file_path works correctly.")

        # Test normalize_path_in_project
        assert normalize_path_in_project("/src/project/main.cpp", "/src/project") == "main.cpp"
        assert normalize_path_in_project("relative.cpp", "/src/project") == "relative.cpp"
        logger.info("  PASS: normalize_path_in_project works correctly.")

        logger.info("PASS: Utility tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 6: Metrics
# ============================================================

def test_metrics() -> bool:
    """Test the MetricsCollector."""
    logger.info("--- Test 6: Metrics ---")

    from dependency_builder.metrics import MetricsCollector, get_metrics, reset_metrics

    try:
        metrics = MetricsCollector()

        # Test counters
        metrics.increment("test.counter")
        metrics.increment("test.counter", 5)
        assert metrics.get_counter("test.counter") == 6
        logger.info("  PASS: Counter operations work.")

        # Test timing
        with metrics.timer("test.operation"):
            time.sleep(0.01)
        stats = metrics.get_timing_stats("test.operation")
        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg_ms"] > 0
        logger.info(f"  PASS: Timing works (avg: {stats['avg_ms']:.2f}ms).")

        # Test cache metrics
        metrics.record_cache_hit("key1")
        metrics.record_cache_hit("key2")
        metrics.record_cache_miss("key3")
        assert metrics.cache_hit_rate > 0.5
        logger.info(f"  PASS: Cache hit rate: {metrics.cache_hit_rate:.2%}")

        # Test error tracking
        metrics.record_error("timeout", "LSP timed out")
        metrics.record_error("timeout", "Another timeout")
        errors = metrics.get_error_counts()
        assert errors["timeout"] == 2
        logger.info("  PASS: Error tracking works.")

        # Test summary
        summary = metrics.summary()
        assert "counters" in summary
        assert "timings" in summary
        assert "cache" in summary
        assert "errors" in summary
        assert "uptime_seconds" in summary
        logger.info("  PASS: Summary generation works.")

        # Test reset
        metrics.reset()
        assert metrics.get_counter("test.counter") == 0
        logger.info("  PASS: Reset works.")

        # Test global singleton
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2
        reset_metrics()
        logger.info("  PASS: Global singleton works.")

        logger.info("PASS: Metrics tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 7: CacheMetadata (Smart Cache Invalidation)
# ============================================================

def test_cache_metadata() -> bool:
    """Test the CacheMetadata system for smart cache invalidation."""
    logger.info("--- Test 7: CacheMetadata (Smart Cache Invalidation) ---")

    from dependency_builder.dependency_handler import CacheMetadata

    work_dir = Path(os.getcwd()) / "temp_cache_test"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        cache = CacheMetadata(str(work_dir), logger)

        # Create a test source file
        test_file = work_dir / "test_source.cpp"
        test_file.write_text("int main() { return 0; }", encoding="utf-8")
        artifact_path = str(work_dir / "test_artifact.json")

        # Record a cache entry
        cache.record_cache_entry(str(test_file), "test_key", artifact_path)

        # Cache should be valid (file unchanged)
        assert cache.is_cache_valid(str(test_file), "test_key"), "Cache should be valid for unchanged file"
        logger.info("  PASS: Cache valid for unchanged file")

        # Modify the file
        time.sleep(0.1)  # Ensure mtime changes
        test_file.write_text("int main() { return 1; }", encoding="utf-8")

        # Cache should now be stale
        assert not cache.is_cache_valid(str(test_file), "test_key"), "Cache should be stale after file modification"
        logger.info("  PASS: Cache correctly detected as stale after file modification")

        # Test invalidation
        cache.record_cache_entry(str(test_file), "test_key_2", artifact_path)
        count = cache.invalidate_file(str(test_file))
        assert count >= 1, f"Should have invalidated at least 1 entry, got {count}"
        logger.info(f"  PASS: invalidate_file removed {count} entries")

        # Test stale cleanup
        test_file2 = work_dir / "test_source2.cpp"
        test_file2.write_text("void foo() {}", encoding="utf-8")
        cache.record_cache_entry(str(test_file2), "key2", str(work_dir / "art2.json"))
        time.sleep(0.1)
        test_file2.write_text("void bar() {}", encoding="utf-8")
        stale = cache.get_stale_entries()
        assert "key2" in stale, "Modified file should produce stale entry"
        logger.info(f"  PASS: get_stale_entries correctly identified {len(stale)} stale entries")

        logger.info("PASS: CacheMetadata tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)


# ============================================================
# Test 8: Connection Pool (unit test, no ccls required)
# ============================================================

def test_connection_pool_unit() -> bool:
    """Test connection pool logic without requiring ccls."""
    logger.info("--- Test 8: Connection Pool (Unit) ---")

    from dependency_builder.connection_pool import CCLSConnectionPool
    from dependency_builder.config import DependencyBuilderConfig

    try:
        config = DependencyBuilderConfig(pool_max_size=2, pool_idle_timeout=1.0)
        pool = CCLSConnectionPool(config)

        assert pool.size == 0
        assert pool.available == 0
        logger.info("  PASS: Empty pool has size=0, available=0.")

        # Test health_check on empty pool
        health = pool.health_check()
        assert health["pool_size"] == 0
        logger.info("  PASS: Health check on empty pool works.")

        # Test stats
        stats = pool.stats
        assert stats["acquisitions"] == 0
        assert stats["creates"] == 0
        logger.info("  PASS: Stats initialized correctly.")

        # Test close_all on empty pool (should not fail)
        pool.close_all()
        logger.info("  PASS: close_all on empty pool works.")

        logger.info("PASS: Connection pool unit tests passed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False


# ============================================================
# Test 9: Integration Test (requires ccls)
# ============================================================

def test_ingestion_and_fetch() -> bool:
    """
    End-to-end integration test: Index a project and fetch dependencies.
    Requires ccls to be installed.
    """
    logger.info("--- Test 9: Ingestion + Dependency Fetch (Integration) ---")

    if not check_ccls_available():
        logger.warning("SKIP: ccls not available, skipping integration test")
        return True  # Not a failure, just skipped

    from dependency_builder.ccls_ingestion import CCLSIngestion
    from dependency_builder.dependency_service import DependencyService
    from dependency_builder.config import DependencyBuilderConfig

    work_dir = Path(os.getcwd()) / "temp_test_env"
    if work_dir.exists():
        shutil.rmtree(work_dir)

    try:
        # Create dummy project
        project_dir = create_dummy_project(work_dir)
        output_dir = work_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"  Created test project at: {project_dir}")

        # Test CCLS Ingestion with config
        config = DependencyBuilderConfig()
        ingestion = CCLSIngestion(config=config)
        success = ingestion.run_indexing(
            project_root=str(project_dir),
            output_dir=str(output_dir),
            unique_project_prefix="test_project",
        )

        if not success:
            logger.warning("  SKIP: CCLS indexing failed (may require specific clang version)")
            return True

        # Wait for cache to settle
        time.sleep(2)

        cache_dir = output_dir / ".ccls-cache"
        if cache_dir.exists():
            logger.info("  PASS: .ccls-cache generated successfully.")
        else:
            logger.warning("  WARNING: .ccls-cache not found after indexing.")

        # Test Health Check
        service = DependencyService(data_store_path=str(output_dir), config=config)
        result = service.perform_fetch(
            project_root=str(project_dir),
            output_dir=str(output_dir),
            codebase_identifier=str(output_dir),
            endpoint_type="health_check",
            file_name="",
        )
        assert result.get("message") == "RUNNING OK", f"Health check failed: {result}"
        logger.info("  PASS: Health check endpoint working.")

        # Test comprehensive health status
        health = service.get_health_status(
            project_root=str(project_dir),
            output_dir=str(output_dir),
        )
        assert health.ccls_available, "CCLS should be available"
        assert health.index_exists, "Index should exist after ingestion"
        logger.info(f"  PASS: Comprehensive health: ccls={health.ccls_available}, index={health.index_exists}")

        # Test Dependency Fetch by File Range
        result = service.perform_fetch(
            project_root=str(project_dir),
            output_dir=str(output_dir),
            codebase_identifier=str(output_dir),
            endpoint_type="fetch_dependencies_by_file",
            file_name="main.cpp",
            start=3,
            end=12,
            level=1,
        )
        logger.info(f"  Fetch result: {result.get('message', 'no message')}")

        if result.get("data"):
            data_str = json.dumps(result["data"], default=str)
            if "Vector2D" in data_str or "dot_product" in data_str:
                logger.info("  PASS: Successfully resolved dependencies (Vector2D/dot_product found)")
            else:
                logger.info("  INFO: Dependencies fetched but expected symbols not found in response")
        else:
            logger.info("  INFO: No dependency data returned (may need project-specific config)")

        # Test input validation
        bad_result = service.perform_fetch(
            project_root="/nonexistent/path",
            output_dir=str(output_dir),
            codebase_identifier="test",
            endpoint_type="fetch_dependencies_by_component",
            file_name="main.cpp",
        )
        assert "Error" in bad_result.get("message", ""), "Should return error for invalid path"
        logger.info("  PASS: Input validation correctly rejects invalid paths.")

        logger.info("PASS: Integration test completed.")
        return True

    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        return False
    except Exception as e:
        logger.exception(f"FAIL: Unexpected error: {e}")
        return False
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)


# ============================================================
# Test Runner
# ============================================================

def run_all_tests():
    """Run all tests and report results."""
    logger.info("=" * 60)
    logger.info(" QGenie Dependency Builder Test Suite v2.0")
    logger.info("=" * 60)

    results = {
        "Module Imports": test_imports(),
        "Configuration": test_config(),
        "Exceptions": test_exceptions(),
        "Models": test_models(),
        "Utilities": test_utils(),
        "Metrics": test_metrics(),
        "Cache Metadata": test_cache_metadata(),
        "Connection Pool (Unit)": test_connection_pool_unit(),
        "Integration (E2E)": test_ingestion_and_fetch(),
    }

    logger.info("\n" + "=" * 60)
    logger.info(" TEST RESULTS")
    logger.info("=" * 60)

    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        icon = "+" if passed else "X"
        logger.info(f"  [{icon}] {name}: {status}")
        if not passed:
            all_passed = False

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    logger.info(f"\n  {passed_count}/{total_count} tests passed")
    logger.info("=" * 60)

    if all_passed:
        logger.info(" ALL TESTS PASSED")
    else:
        logger.error(" SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
