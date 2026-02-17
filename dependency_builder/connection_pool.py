"""
CCLS process connection pool for the dependency_builder package.

Manages a pool of CCLSCodeNavigator instances to avoid the overhead
of spawning a new ccls process for every single request. Provides
thread-safe acquisition/release with health checking and idle timeout.
"""

import logging
import time
import threading
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """Wrapper around a CCLSCodeNavigator with pool metadata."""
    navigator: object  # CCLSCodeNavigator (lazy import to avoid circular)
    project_root: str
    cache_path: str
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    in_use: bool = False
    request_count: int = 0

    @property
    def idle_seconds(self) -> float:
        """Seconds since this connection was last used."""
        return time.time() - self.last_used_at

    @property
    def age_seconds(self) -> float:
        """Total age of this connection in seconds."""
        return time.time() - self.created_at


class CCLSConnectionPool:
    """
    Thread-safe connection pool for CCLSCodeNavigator instances.

    Maintains a pool of live ccls processes that can be reused across
    multiple dependency fetch requests, avoiding the startup cost of
    spawning a new process each time.

    Usage:
        pool = CCLSConnectionPool(config)
        conn = pool.acquire(project_root, cache_path, logger)
        try:
            # Use conn.navigator for LSP operations
            result = conn.navigator.getDefinition(doc, pos)
        finally:
            pool.release(conn)

    Or with context manager:
        with pool.connection(project_root, cache_path, logger) as nav:
            result = nav.getDefinition(doc, pos)
    """

    def __init__(self, config: DependencyBuilderConfig = None):
        self._config = config or DEFAULT_CONFIG
        self._pool: List[PooledConnection] = []
        self._lock = threading.Lock()
        self._stats = {
            "acquisitions": 0,
            "releases": 0,
            "creates": 0,
            "evictions": 0,
            "health_check_failures": 0,
        }

    @property
    def size(self) -> int:
        """Current number of connections in the pool."""
        with self._lock:
            return len(self._pool)

    @property
    def available(self) -> int:
        """Number of idle (not in-use) connections."""
        with self._lock:
            return sum(1 for c in self._pool if not c.in_use)

    @property
    def stats(self) -> Dict[str, int]:
        """Pool statistics."""
        with self._lock:
            return dict(self._stats)

    def acquire(
        self,
        project_root: str,
        cache_path: str,
        nav_logger: logging.Logger,
    ) -> PooledConnection:
        """
        Acquire a connection from the pool.

        If a matching idle connection exists, reuses it. Otherwise,
        creates a new CCLSCodeNavigator if the pool isn't full.
        Evicts idle connections if the pool is full.

        Args:
            project_root: The C/C++ project root directory.
            cache_path: Path to the ccls cache directory.
            nav_logger: Logger for the navigator instance.

        Returns:
            A PooledConnection with an active navigator.

        Raises:
            dependency_builder.exceptions.PoolExhaustedError: If pool is full and all connections are in use.
        """
        import os
        abs_root = os.path.abspath(project_root)
        abs_cache = os.path.abspath(cache_path)

        with self._lock:
            # 1. Try to find an idle connection for the same project
            for conn in self._pool:
                if (not conn.in_use
                        and conn.project_root == abs_root
                        and conn.cache_path == abs_cache):
                    # Health check: verify process is still alive
                    if self._is_alive(conn):
                        conn.in_use = True
                        conn.last_used_at = time.time()
                        conn.request_count += 1
                        self._stats["acquisitions"] += 1
                        logger.debug(
                            f"Reusing pooled connection for {abs_root} "
                            f"(requests: {conn.request_count})"
                        )
                        return conn
                    else:
                        # Dead connection, remove it
                        self._stats["health_check_failures"] += 1
                        self._remove_connection(conn)

            # 2. Evict idle connections for OTHER projects if pool is full
            if len(self._pool) >= self._config.pool_max_size:
                evicted = self._evict_idle()
                if not evicted:
                    # All connections are in use
                    from dependency_builder.exceptions import PoolExhaustedError
                    raise PoolExhaustedError(self._config.pool_max_size)

            # 3. Create a new connection
            conn = self._create_connection(abs_root, abs_cache, nav_logger)
            conn.in_use = True
            self._pool.append(conn)
            self._stats["acquisitions"] += 1
            self._stats["creates"] += 1
            logger.info(f"Created new pooled connection for {abs_root} (pool size: {len(self._pool)})")
            return conn

    def release(self, conn: PooledConnection) -> None:
        """
        Release a connection back to the pool.

        Args:
            conn: The connection to release.
        """
        with self._lock:
            conn.in_use = False
            conn.last_used_at = time.time()
            self._stats["releases"] += 1
            logger.debug(f"Released connection for {conn.project_root}")

    def connection(self, project_root: str, cache_path: str, nav_logger: logging.Logger):
        """
        Context manager for acquiring and releasing a connection.

        Usage:
            with pool.connection(root, cache, logger) as nav:
                result = nav.getDefinition(doc, pos)
        """
        return _PoolConnectionContext(self, project_root, cache_path, nav_logger)

    def close_all(self) -> None:
        """Close all connections in the pool and clean up resources."""
        with self._lock:
            for conn in self._pool:
                self._kill_connection(conn)
            self._pool.clear()
            logger.info("Connection pool closed, all connections terminated")

    def evict_idle(self, max_idle_seconds: float = None) -> int:
        """
        Evict idle connections that have exceeded the idle timeout.

        Args:
            max_idle_seconds: Override for the configured idle timeout.

        Returns:
            Number of connections evicted.
        """
        with self._lock:
            return self._evict_idle(max_idle_seconds)

    def health_check(self) -> Dict[str, object]:
        """
        Check health of all connections in the pool.

        Returns:
            Dict with pool health information.
        """
        with self._lock:
            alive = 0
            dead = 0
            in_use = 0
            idle = 0

            dead_conns = []
            for conn in self._pool:
                if conn.in_use:
                    in_use += 1
                else:
                    idle += 1

                if self._is_alive(conn):
                    alive += 1
                else:
                    dead += 1
                    dead_conns.append(conn)

            # Clean up dead connections
            for conn in dead_conns:
                self._remove_connection(conn)
                self._stats["health_check_failures"] += 1

            return {
                "pool_size": len(self._pool),
                "alive": alive,
                "dead": dead,
                "in_use": in_use,
                "idle": idle,
                "stats": dict(self._stats),
            }

    # --- Private Methods ---

    def _create_connection(
        self,
        project_root: str,
        cache_path: str,
        nav_logger: logging.Logger,
    ) -> PooledConnection:
        """Create a new CCLSCodeNavigator and wrap it in a PooledConnection."""
        from dependency_builder.ccls_code_navigator import CCLSCodeNavigator

        navigator = CCLSCodeNavigator(
            project_root=project_root,
            cache_path=cache_path,
            logger=nav_logger,
        )
        return PooledConnection(
            navigator=navigator,
            project_root=project_root,
            cache_path=cache_path,
        )

    def _is_alive(self, conn: PooledConnection) -> bool:
        """Check if the ccls process backing a connection is still alive."""
        try:
            nav = conn.navigator
            if hasattr(nav, "ccls_process") and nav.ccls_process:
                return nav.ccls_process.poll() is None
            return False
        except Exception:
            return False

    def _kill_connection(self, conn: PooledConnection) -> None:
        """Kill the ccls process backing a connection."""
        try:
            nav = conn.navigator
            if hasattr(nav, "killCCLSProcess"):
                nav.killCCLSProcess()
        except Exception as e:
            logger.debug(f"Error killing pooled connection: {e}")

    def _remove_connection(self, conn: PooledConnection) -> None:
        """Remove and kill a connection from the pool."""
        self._kill_connection(conn)
        if conn in self._pool:
            self._pool.remove(conn)
        self._stats["evictions"] += 1

    def _evict_idle(self, max_idle_seconds: float = None) -> int:
        """
        Evict idle connections (must be called with lock held).

        Returns the number of connections evicted.
        """
        timeout = max_idle_seconds or self._config.pool_idle_timeout
        evicted = 0

        to_evict = [
            conn for conn in self._pool
            if not conn.in_use and conn.idle_seconds > timeout
        ]

        if not to_evict:
            # If no idle-timeout connections, evict the oldest idle connection
            idle_conns = [c for c in self._pool if not c.in_use]
            if idle_conns:
                to_evict = [min(idle_conns, key=lambda c: c.last_used_at)]

        for conn in to_evict:
            self._remove_connection(conn)
            evicted += 1

        if evicted:
            logger.info(f"Evicted {evicted} idle connections from pool")

        return evicted

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            self.close_all()
        except Exception:
            pass


class _PoolConnectionContext:
    """Context manager for pool.connection()."""

    def __init__(self, pool: CCLSConnectionPool, project_root: str,
                 cache_path: str, nav_logger: logging.Logger):
        self._pool = pool
        self._project_root = project_root
        self._cache_path = cache_path
        self._logger = nav_logger
        self._conn: Optional[PooledConnection] = None

    def __enter__(self):
        self._conn = self._pool.acquire(
            self._project_root, self._cache_path, self._logger
        )
        return self._conn.navigator

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn:
            self._pool.release(self._conn)
        return False  # Don't suppress exceptions
