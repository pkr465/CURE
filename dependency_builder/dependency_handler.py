import logging
import json
import os
import time
from typing import Optional, Dict, Any, Union, List

# Internal imports
from dependency_builder.ccls_dependency_builder import CCLSDependencyBuilder
from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
from dependency_builder.utils import safe_filename, compute_content_hash
from dependency_builder.metrics import get_metrics


class CacheMetadata:
    """
    Tracks file modification times and content hashes to enable smart
    cache invalidation. Only re-parses files that have actually changed.
    """

    def __init__(self, output_dir: str, logger: logging.Logger,
                 config: DependencyBuilderConfig = None):
        self.output_dir = output_dir
        self.logger = logger
        self.config = config or DEFAULT_CONFIG
        self._metrics = get_metrics()
        self._metadata: Dict[str, Any] = {}
        self._metadata_path = os.path.join(output_dir, self.config.cache_metadata_filename)
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load existing cache metadata from disk."""
        if os.path.exists(self._metadata_path):
            try:
                with open(self._metadata_path, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Corrupted cache metadata, resetting: {e}")
                self._metadata = {}

    def _save_metadata(self) -> None:
        """Persist cache metadata to disk."""
        try:
            os.makedirs(os.path.dirname(self._metadata_path), exist_ok=True)
            with open(self._metadata_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2)
        except IOError as e:
            self.logger.warning(f"Failed to save cache metadata: {e}")

    def _get_file_fingerprint(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get a fingerprint of a file using mtime + size for fast comparison,
        with an optional content hash for collision safety.
        """
        try:
            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                return None
            stat = os.stat(abs_path)
            return {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "path": abs_path,
            }
        except OSError as e:
            self.logger.warning(f"Cannot stat file {file_path}: {e}")
            return None

    def is_cache_valid(self, source_file: str, cache_key: str) -> bool:
        """
        Check if a cached artifact is still valid by comparing the source
        file's current mtime/size against what was recorded when the cache
        was created.

        Returns True if cache is valid (file unchanged), False if stale.
        """
        entry = self._metadata.get(cache_key)
        if not entry:
            return False

        fingerprint = self._get_file_fingerprint(source_file)
        if not fingerprint:
            return False

        stored = entry.get("source_fingerprint", {})

        # Fast check: mtime + size comparison
        if (stored.get("mtime") == fingerprint["mtime"]
                and stored.get("size") == fingerprint["size"]):
            return True

        # If mtime changed but size is the same, do a content hash check
        # (handles cases where a file was touched but not modified)
        if stored.get("size") == fingerprint["size"]:
            current_hash = self._compute_content_hash(fingerprint["path"])
            if current_hash and current_hash == stored.get("content_hash"):
                # File was touched but content unchanged - update mtime in metadata
                stored["mtime"] = fingerprint["mtime"]
                self._save_metadata()
                return True

        return False

    def record_cache_entry(self, source_file: str, cache_key: str, artifact_path: str) -> None:
        """
        Record that a cache artifact was created for the given source file.
        Stores the file's fingerprint and a content hash for future validation.
        """
        fingerprint = self._get_file_fingerprint(source_file)
        if not fingerprint:
            return

        content_hash = self._compute_content_hash(fingerprint["path"])

        self._metadata[cache_key] = {
            "artifact_path": artifact_path,
            "source_fingerprint": {
                "mtime": fingerprint["mtime"],
                "size": fingerprint["size"],
                "content_hash": content_hash,
            },
            "created_at": time.time(),
            "source_file": fingerprint["path"],
        }
        self._save_metadata()

    def invalidate(self, cache_key: str) -> None:
        """Explicitly invalidate a cache entry."""
        if cache_key in self._metadata:
            del self._metadata[cache_key]
            self._save_metadata()

    def invalidate_file(self, source_file: str) -> int:
        """
        Invalidate ALL cache entries associated with a given source file.
        Returns the number of entries invalidated.
        """
        abs_path = os.path.abspath(source_file)
        keys_to_remove = [
            k for k, v in self._metadata.items()
            if v.get("source_file") == abs_path
        ]
        for k in keys_to_remove:
            del self._metadata[k]
        if keys_to_remove:
            self._save_metadata()
        return len(keys_to_remove)

    def get_stale_entries(self) -> List[str]:
        """Return list of cache keys whose source files have been modified."""
        stale = []
        for cache_key, entry in self._metadata.items():
            source_file = entry.get("source_file", "")
            if source_file and not self.is_cache_valid(source_file, cache_key):
                stale.append(cache_key)
        return stale

    def cleanup_stale(self) -> int:
        """
        Remove stale cache entries and their artifact files.
        Returns the number of entries cleaned up.
        """
        stale_keys = self.get_stale_entries()
        for key in stale_keys:
            entry = self._metadata.get(key, {})
            artifact = entry.get("artifact_path", "")
            if artifact and os.path.exists(artifact):
                try:
                    os.remove(artifact)
                    self.logger.debug(f"Removed stale cache artifact: {artifact}")
                except OSError:
                    pass
            del self._metadata[key]

        if stale_keys:
            self._save_metadata()
            self.logger.info(f"Cleaned up {len(stale_keys)} stale cache entries")
        return len(stale_keys)

    @staticmethod
    def _compute_content_hash(file_path: str) -> Optional[str]:
        """Compute a fast content hash (MD5) for cache collision detection.
        Delegates to shared utility."""
        return compute_content_hash(file_path)


class DependencyFetcher:
    """
    Handler class to process dependency fetch requests.
    Dispatches requests to the CCLSDependencyBuilder based on endpoint type.
    Includes smart caching with file-modification-based invalidation.
    """

    def __init__(self, config: DependencyBuilderConfig = None):
        self._cache_metadata: Optional[CacheMetadata] = None
        self.config = config or DEFAULT_CONFIG
        self._metrics = get_metrics()

    def _get_cache_metadata(self, output_dir: str, logger: logging.Logger) -> CacheMetadata:
        """Lazy-initialize the cache metadata tracker."""
        if self._cache_metadata is None:
            self._cache_metadata = CacheMetadata(output_dir, logger)
        return self._cache_metadata

    def _get_artifact_path(self, output_dir: str, raw_filename: str) -> str:
        """
        Generates a safe, collision-resistant path for a cache artifact.
        Delegates to shared utility for filename safety.
        """
        target_dir = output_dir if output_dir else "out"
        safe_name = safe_filename(
            raw_filename,
            max_length=self.config.max_filename_length,
            hash_suffix_length=self.config.hash_suffix_length,
            prefix_length=self.config.filename_prefix_length,
        )
        return os.path.join(target_dir, safe_name)

    def _load_cached_data(
        self, artifact_path: str, source_file: str, cache_key: str,
        cache_meta: CacheMetadata, logger: logging.Logger
    ) -> Optional[Any]:
        """
        Attempts to load data from cache, but only if the source file
        hasn't been modified since the cache was created.
        Returns None on cache miss or stale cache.
        """
        if not os.path.exists(artifact_path):
            logger.debug(f"Cache miss (no artifact): {cache_key}")
            self._metrics.record_cache_miss(cache_key)
            return None

        # Check if source file has changed since cache was written
        if not cache_meta.is_cache_valid(source_file, cache_key):
            logger.info(f"Cache stale (file modified): {cache_key}")
            self._metrics.record_cache_stale(cache_key)
            # Remove the stale artifact
            try:
                os.remove(artifact_path)
            except OSError:
                pass
            cache_meta.invalidate(cache_key)
            return None

        try:
            logger.info(f"Cache hit: {cache_key}")
            self._metrics.record_cache_hit(cache_key)
            with open(artifact_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Corrupted cache file {artifact_path}: {e}")
            cache_meta.invalidate(cache_key)
            return None

    def _save_data(
        self, artifact_path: str, data: Any, source_file: str,
        cache_key: str, cache_meta: CacheMetadata, logger: logging.Logger
    ) -> None:
        """Saves data to cache and records the source file fingerprint."""
        try:
            target_dir = os.path.dirname(artifact_path)
            if target_dir and not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            with open(artifact_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            # Record the source file's fingerprint for future invalidation checks
            cache_meta.record_cache_entry(source_file, cache_key, artifact_path)
            logger.debug(f"Cached artifact: {artifact_path}")

        except (IOError, TypeError) as e:
            logger.warning(f"Failed to save cache artifact {artifact_path}: {e}")

    def process_message(
        self,
        repo_path: str,
        data_store_path: str,
        unique_project_prefix: str,
        endpoint_type: str,
        function_name: Optional[str] = None,
        file_name: Optional[str] = None,
        start: Optional[Union[str, int]] = None,
        end: Optional[Union[str, int]] = None,
        line: Optional[Union[str, int]] = None,
        character: Optional[Union[str, int]] = None,
        level: Optional[Union[str, int]] = None,
        project_logger: Optional[logging.Logger] = None
    ) -> Dict[str, Any]:
        """
        Main dispatcher for dependency fetch requests.
        Routes to the appropriate CCLSDependencyBuilder method based on endpoint_type.
        """
        # Ensure we have a logger
        if not project_logger:
            project_logger = logging.getLogger("DependencyFetcher")
            project_logger.setLevel(logging.INFO)

        # Input normalization
        try:
            level = int(level) if level is not None else 1
        except (ValueError, TypeError):
            level = 1

        # Resolve paths
        project_root = os.path.abspath(repo_path)
        abs_output_dir = os.path.abspath(data_store_path) if data_store_path else os.path.join(project_root, "out")
        ccls_index_path = os.path.join(abs_output_dir, ".ccls-cache")

        # Initialize smart cache
        cache_meta = self._get_cache_metadata(abs_output_dir, project_logger)

        # Clean up stale cache entries on first access
        cache_meta.cleanup_stale()

        # Normalize file_name to relative path
        if file_name and os.path.isabs(file_name):
            try:
                if file_name.startswith(project_root):
                    file_name = os.path.relpath(file_name, project_root)
            except ValueError:
                project_logger.warning(f"Could not relativize path: {file_name}")

        # Resolve the absolute source file path for fingerprinting
        source_file_abs = os.path.join(project_root, file_name) if file_name else ""

        # Health check endpoint
        if "health_check" in endpoint_type:
            return {"message": "RUNNING OK", "data": {}}

        # Validate file_name is provided for all fetch endpoints
        if not file_name:
            return {"message": "Error: file_name is mandatory", "data": {}}

        # Initialize the CCLS builder
        fetcher = CCLSDependencyBuilder(project_root, ccls_index_path, project_logger)

        try:
            # --- Fetch by Component Name ---
            if "fetch_dependencies_by_component" in endpoint_type:
                if not function_name:
                    return {"message": "Error: function_name is mandatory", "data": {}}

                cache_key = f"comp:{os.path.basename(file_name)}:{function_name}"
                artifact_name = f"fetch_comp_{os.path.basename(file_name)}_{function_name}.json"
                artifact_path = self._get_artifact_path(abs_output_dir, artifact_name)

                cached_data = self._load_cached_data(
                    artifact_path, source_file_abs, cache_key, cache_meta, project_logger
                )
                if cached_data is not None:
                    status_msg = "success (cached)" if cached_data else "No dependencies found (cached)"
                    return {"message": status_msg, "data": cached_data}

                # Cache miss - fetch from CCLS
                fn_dependencies = fetcher.get_dependency_component(
                    file_path=file_name,
                    component_name=function_name,
                    level=level
                )

                data = fn_dependencies if fn_dependencies else {}
                status_msg = "success" if fn_dependencies else "No dependencies found"

                self._save_data(
                    artifact_path, data, source_file_abs, cache_key, cache_meta, project_logger
                )
                return {"message": status_msg, "data": data}

            # --- Fetch by Line and Character (Position) ---
            elif "fetch_dependencies_by_line_character" in endpoint_type:
                if line is None:
                    return {"message": "Error: line is mandatory", "data": {}}
                if character is None:
                    return {"message": "Error: character is mandatory", "data": {}}

                try:
                    line_no = int(line)
                    char_no = int(character)
                except (ValueError, TypeError):
                    return {"message": "Error: line and character must be integers", "data": {}}

                cache_key = f"pos:{os.path.basename(file_name)}:{line_no}:{char_no}"
                artifact_name = f"fetch_pos_{os.path.basename(file_name)}_{line_no}_{char_no}.json"
                artifact_path = self._get_artifact_path(abs_output_dir, artifact_name)

                cached_data = self._load_cached_data(
                    artifact_path, source_file_abs, cache_key, cache_meta, project_logger
                )
                if cached_data is not None:
                    status_msg = "success (cached)" if cached_data else "No dependencies found (cached)"
                    return {"message": status_msg, "data": cached_data}

                fn_dependencies = fetcher.get_dependency_line_char(
                    file_path=file_name,
                    line_no=line_no,
                    character_no=char_no,
                    level=level
                )

                data = fn_dependencies if fn_dependencies else {}
                status_msg = "success" if fn_dependencies else "No dependencies found"

                self._save_data(
                    artifact_path, data, source_file_abs, cache_key, cache_meta, project_logger
                )
                return {"message": status_msg, "data": data}

            # --- Fetch by File (Range/Diff) ---
            elif "fetch_dependencies_by_file" in endpoint_type:
                start_val = 0
                if start is not None:
                    try:
                        start_val = int(start)
                    except (ValueError, TypeError):
                        pass

                end_val = float("inf")
                if end is not None:
                    try:
                        end_val = int(end)
                    except (ValueError, TypeError):
                        pass

                cache_key = f"file:{os.path.basename(file_name)}:{start_val}:{end_val}"
                artifact_name = f"fetch_file_{os.path.basename(file_name)}_{start_val}_{end_val}.json"
                artifact_path = self._get_artifact_path(abs_output_dir, artifact_name)

                cached_data = self._load_cached_data(
                    artifact_path, source_file_abs, cache_key, cache_meta, project_logger
                )
                if cached_data is not None:
                    status_msg = "success (cached)" if cached_data else "No dependencies found (cached)"
                    return {"message": status_msg, "data": cached_data}

                fn_dependencies = fetcher.get_dependency_diff(
                    file_path=file_name,
                    start=start_val,
                    end=end_val,
                    level=level
                )

                data = fn_dependencies if fn_dependencies else []
                status_msg = "success" if fn_dependencies else "No dependencies found"

                self._save_data(
                    artifact_path, data, source_file_abs, cache_key, cache_meta, project_logger
                )
                return {"message": status_msg, "data": data}

            else:
                return {"message": f"Error: Unknown endpoint type '{endpoint_type}'", "data": {}}

        except Exception as e:
            project_logger.exception(f"Critical error in dependency fetcher: {e}")
            return {"message": f"Error in dependencies fetch: {str(e)}", "data": {}}
