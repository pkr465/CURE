"""
Observability and metrics for the dependency_builder package.

Provides lightweight instrumentation for tracking performance,
cache hit rates, error counts, and operational health without
requiring external dependencies (e.g., Prometheus, StatsD).

Metrics can be logged periodically or exported as a dict for
integration with monitoring systems.
"""

import time
import logging
import threading
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@dataclass
class TimerResult:
    """Result of a timed operation."""
    operation: str
    duration_ms: float
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class MetricsCollector:
    """
    Thread-safe metrics collector for dependency_builder operations.

    Tracks:
        - Operation counts (successes, failures)
        - Timing data (min, max, avg, total)
        - Cache performance (hits, misses, evictions)
        - CCLS process lifecycle events
        - Error categories and frequencies

    Usage:
        metrics = MetricsCollector()

        # Record an operation duration
        with metrics.timer("fetch_dependencies"):
            result = fetch_deps(...)

        # Record a cache hit/miss
        metrics.record_cache_hit("fetch_comp_main.cpp_foo")
        metrics.record_cache_miss("fetch_pos_main.cpp_10_5")

        # Get summary
        print(metrics.summary())
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._timings: Dict[str, list] = {}
        self._errors: Dict[str, int] = {}
        self._start_time = time.time()
        self._callbacks: list = []

    # --- Counter Operations ---

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named counter."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def get_counter(self, name: str) -> int:
        """Get the current value of a counter."""
        with self._lock:
            return self._counters.get(name, 0)

    # --- Timing Operations ---

    @contextmanager
    def timer(self, operation: str, metadata: Dict[str, Any] = None):
        """
        Context manager to time an operation.

        Usage:
            with metrics.timer("indexing"):
                ingestion.run_indexing(...)
        """
        start = time.time()
        success = True
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            duration_ms = (time.time() - start) * 1000
            self._record_timing(operation, duration_ms, success, metadata)

    def record_timing(self, operation: str, duration_ms: float,
                      success: bool = True, metadata: Dict[str, Any] = None) -> None:
        """Manually record a timing measurement."""
        self._record_timing(operation, duration_ms, success, metadata)

    def _record_timing(self, operation: str, duration_ms: float,
                       success: bool, metadata: Dict[str, Any] = None) -> None:
        """Internal timing recording."""
        result = TimerResult(
            operation=operation,
            duration_ms=duration_ms,
            success=success,
            metadata=metadata or {},
        )

        with self._lock:
            if operation not in self._timings:
                self._timings[operation] = []
            self._timings[operation].append(result)

            # Update counters
            suffix = "success" if success else "failure"
            key = f"{operation}.{suffix}"
            self._counters[key] = self._counters.get(key, 0) + 1

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(result)
            except Exception:
                pass

    def get_timing_stats(self, operation: str) -> Optional[Dict[str, float]]:
        """
        Get timing statistics for an operation.

        Returns dict with: count, total_ms, avg_ms, min_ms, max_ms, success_rate
        """
        with self._lock:
            entries = self._timings.get(operation, [])
            if not entries:
                return None

            durations = [e.duration_ms for e in entries]
            successes = sum(1 for e in entries if e.success)

            return {
                "count": len(entries),
                "total_ms": sum(durations),
                "avg_ms": sum(durations) / len(durations),
                "min_ms": min(durations),
                "max_ms": max(durations),
                "success_rate": successes / len(entries) if entries else 0.0,
            }

    # --- Cache Metrics ---

    def record_cache_hit(self, cache_key: str = "") -> None:
        """Record a cache hit."""
        self.increment("cache.hits")
        logger.debug(f"Cache HIT: {cache_key}")

    def record_cache_miss(self, cache_key: str = "") -> None:
        """Record a cache miss."""
        self.increment("cache.misses")
        logger.debug(f"Cache MISS: {cache_key}")

    def record_cache_eviction(self, cache_key: str = "") -> None:
        """Record a cache eviction."""
        self.increment("cache.evictions")

    def record_cache_stale(self, cache_key: str = "") -> None:
        """Record a stale cache detection."""
        self.increment("cache.stale")

    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate (0.0 to 1.0)."""
        with self._lock:
            hits = self._counters.get("cache.hits", 0)
            misses = self._counters.get("cache.misses", 0)
            total = hits + misses
            return hits / total if total > 0 else 0.0

    # --- Error Tracking ---

    def record_error(self, error_type: str, message: str = "") -> None:
        """Record an error occurrence."""
        with self._lock:
            self._errors[error_type] = self._errors.get(error_type, 0) + 1
            self.increment("errors.total")
        logger.debug(f"Error recorded: {error_type} - {message}")

    def get_error_counts(self) -> Dict[str, int]:
        """Get all error counts by type."""
        with self._lock:
            return dict(self._errors)

    # --- CCLS Process Metrics ---

    def record_process_start(self) -> None:
        """Record a ccls process start."""
        self.increment("ccls.process_starts")

    def record_process_kill(self) -> None:
        """Record a ccls process termination."""
        self.increment("ccls.process_kills")

    def record_process_crash(self) -> None:
        """Record a ccls process crash."""
        self.increment("ccls.process_crashes")

    # --- LSP Metrics ---

    def record_lsp_call(self, method: str) -> None:
        """Record an LSP method call."""
        self.increment(f"lsp.calls.{method}")
        self.increment("lsp.calls.total")

    # --- Callbacks ---

    def on_timing(self, callback: Callable[[TimerResult], None]) -> None:
        """Register a callback to be called on every timing recording."""
        self._callbacks.append(callback)

    # --- Summary and Export ---

    def summary(self) -> Dict[str, Any]:
        """
        Generate a comprehensive metrics summary.

        Returns a dict suitable for logging or API responses.
        """
        with self._lock:
            uptime = time.time() - self._start_time

            timing_stats = {}
            for op in self._timings:
                stats = self.get_timing_stats(op)
                if stats:
                    timing_stats[op] = stats

            return {
                "uptime_seconds": round(uptime, 2),
                "counters": dict(self._counters),
                "timings": timing_stats,
                "errors": dict(self._errors),
                "cache": {
                    "hits": self._counters.get("cache.hits", 0),
                    "misses": self._counters.get("cache.misses", 0),
                    "hit_rate": round(self.cache_hit_rate, 4),
                    "evictions": self._counters.get("cache.evictions", 0),
                    "stale": self._counters.get("cache.stale", 0),
                },
                "ccls": {
                    "process_starts": self._counters.get("ccls.process_starts", 0),
                    "process_kills": self._counters.get("ccls.process_kills", 0),
                    "process_crashes": self._counters.get("ccls.process_crashes", 0),
                },
            }

    def log_summary(self, level: int = logging.INFO) -> None:
        """Log the metrics summary at the specified log level."""
        summary = self.summary()
        logger.log(level, f"Dependency Builder Metrics: {summary}")

    def reset(self) -> None:
        """Reset all metrics. Useful for testing."""
        with self._lock:
            self._counters.clear()
            self._timings.clear()
            self._errors.clear()
            self._start_time = time.time()


# Module-level singleton for easy access
_global_metrics: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Get or create the global MetricsCollector singleton."""
    global _global_metrics
    with _metrics_lock:
        if _global_metrics is None:
            _global_metrics = MetricsCollector()
        return _global_metrics


def reset_metrics() -> None:
    """Reset the global metrics collector."""
    global _global_metrics
    with _metrics_lock:
        if _global_metrics:
            _global_metrics.reset()
