import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# Internal imports
from dependency_builder.dependency_handler import DependencyFetcher, CacheMetadata
from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
from dependency_builder.models import FetchRequest, FetchResponse, HealthStatus, EndpointType
from dependency_builder.exceptions import IndexNotFoundError, ValidationError
from dependency_builder.metrics import get_metrics

# Configure Logger
logger = logging.getLogger(__name__)


class DependencyService:
    """
    High-level service to orchestrate dependency fetching.
    Acts as the bridge between the Agent/Main and the lower-level DependencyFetcher.
    """

    def __init__(self, data_store_path: Optional[str] = None,
                 config: DependencyBuilderConfig = None):
        self.data_store_path = data_store_path
        self.config = config or DEFAULT_CONFIG
        self._fetcher = DependencyFetcher(config=self.config)
        self._metrics = get_metrics()

    def _check_indexing_status(
        self, unique_project_prefix: str, output_dir: str
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Verifies if the CCLS cache exists for the given project.
        Returns (True, None) if cache exists, (False, error_payload) otherwise.
        """
        abs_output_dir = os.path.abspath(output_dir)
        path_to_check = os.path.join(abs_output_dir, ".ccls-cache")

        if not Path(path_to_check).exists():
            msg = (
                f"Dependency Graph not generated yet for '{unique_project_prefix}'. "
                f"Ensure codebase ingestion/indexing is complete. Missing: {path_to_check}"
            )
            return False, {"message": msg, "data": {}}

        return True, None

    def _validate_inputs(
        self,
        project_root: str,
        output_dir: str,
        endpoint_type: str,
        file_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Validate inputs before processing. Returns an error payload if invalid, None if OK.
        """
        if not project_root or not os.path.isdir(project_root):
            return {"message": f"Error: Invalid project_root: {project_root}", "data": {}}
        if not output_dir:
            return {"message": "Error: output_dir is required", "data": {}}
        if not endpoint_type or not endpoint_type.strip():
            return {"message": "Error: endpoint_type is required", "data": {}}
        # file_name is required for all non-health-check endpoints
        if "health_check" not in endpoint_type and not file_name:
            return {"message": "Error: file_name is required for fetch endpoints", "data": {}}
        return None

    def get_health_status(self, project_root: str = "", output_dir: str = "") -> HealthStatus:
        """
        Comprehensive health check for the dependency builder system.
        Verifies: ccls availability, index existence, cache writability, libclang.
        """
        from dependency_builder.ccls_ingestion import CCLSIngestion

        status = HealthStatus()

        # Check ccls availability
        is_valid, version_info = CCLSIngestion.check_ccls_version(
            self.config.ccls_executable, config=self.config
        )
        status.ccls_available = is_valid
        status.ccls_version = version_info

        # Check index existence
        if output_dir:
            cache_path = os.path.join(os.path.abspath(output_dir), ".ccls-cache")
            status.index_exists = os.path.isdir(cache_path)

        # Check cache writability
        if output_dir:
            test_path = os.path.join(os.path.abspath(output_dir), ".cache_write_test")
            try:
                with open(test_path, "w") as f:
                    f.write("test")
                os.remove(test_path)
                status.cache_writable = True
            except (IOError, OSError):
                status.cache_writable = False

        # Check libclang availability
        try:
            from clang.cindex import Index
            idx = Index.create()
            status.libclang_loaded = idx is not None
        except Exception:
            status.libclang_loaded = False

        # Check cache staleness
        if output_dir:
            try:
                cache = CacheMetadata(output_dir, logger, config=self.config)
                stale = cache.get_stale_entries()
                status.stale_cache_entries = len(stale)
                status.total_cache_entries = len(cache._metadata)
            except Exception:
                pass

        return status

    def invalidate_cache_for_file(self, output_dir: str, source_file: str) -> int:
        """
        Public API: Invalidate all cached dependency data for a given source file.
        Call this after a file has been modified and before re-fetching its dependencies.
        Returns the number of cache entries invalidated.
        """
        cache = CacheMetadata(output_dir, logger, config=self.config)
        count = cache.invalidate_file(source_file)
        if count > 0:
            logger.info(f"Invalidated {count} cache entries for {source_file}")
        return count

    def perform_fetch(
        self,
        project_root: str,
        output_dir: str,
        codebase_identifier: str,
        endpoint_type: str,
        file_name: str,
        function_name: Optional[str] = None,
        level: Optional[int] = 1,
        line: Optional[int] = None,
        character: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for Agents to request dependency information.
        Validates inputs, checks indexing status, and dispatches to the fetcher.
        """
        self.data_store_path = output_dir

        # Clean the endpoint string
        clean_endpoint = endpoint_type.strip()

        # Input validation
        validation_error = self._validate_inputs(project_root, output_dir, clean_endpoint, file_name)
        if validation_error:
            logger.warning(f"Input validation failed: {validation_error['message']}")
            return validation_error

        try:
            # 1. Check Indexing Status
            is_indexed, error_payload = self._check_indexing_status(codebase_identifier, output_dir)
            if not is_indexed:
                logger.warning(f"Indexing check failed: {error_payload['message']}")
                return error_payload

            # 2. Process Request via DependencyFetcher
            logger.info(f"Processing {clean_endpoint} request for {file_name}")
            output_payload = self._fetcher.process_message(
                repo_path=project_root,
                data_store_path=output_dir,
                unique_project_prefix=codebase_identifier,
                endpoint_type=clean_endpoint,
                function_name=function_name,
                file_name=file_name,
                start=start,
                end=end,
                line=line,
                character=character,
                level=level,
                project_logger=logger,
            )
            logger.info(f"Fetch complete: {output_payload.get('message', 'unknown')}")
            return output_payload

        except Exception as e:
            logger.error(f"Error in DependencyService: {e}", exc_info=True)
            return {
                "message": f"Critical error in dependency service: {str(e)}",
                "data": {},
            }


# --- Main Execution Block (for testing) ---
if __name__ == "__main__":
    service = DependencyService()
    TEST_ROOT = "/path/to/codebase"
    TEST_OUT = "./out"

    if os.path.exists(TEST_ROOT):
        result = service.perform_fetch(
            project_root=TEST_ROOT,
            output_dir=TEST_OUT,
            codebase_identifier="test",
            endpoint_type="health_check",
            file_name="",
        )
        print(result)