import subprocess
import os
import json
import logging
import shutil
import re
from typing import Optional, Dict, Tuple

from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
from dependency_builder.exceptions import (
    CCLSNotFoundError,
    CCLSVersionError,
    IndexingFailedError,
    IndexingTimeoutError,
)
from dependency_builder.metrics import get_metrics

# Configure Logger
logger = logging.getLogger(__name__)


class CCLSIngestion:
    """
    Handles the initialization and execution of CCLS indexing for C/C++ codebases.
    """

    def __init__(self, env_config: Optional[Dict[str, str]] = None,
                 config: DependencyBuilderConfig = None):
        self.env = os.environ.copy()
        if env_config:
            self.env.update(env_config)
        self.config = config or DEFAULT_CONFIG
        self._metrics = get_metrics()

    @staticmethod
    def check_ccls_version(ccls_executable: str = "ccls",
                           config: DependencyBuilderConfig = None) -> Tuple[bool, str]:
        """
        Check if ccls is available and meets minimum version requirements.
        Returns (is_valid, version_string).
        """
        cfg = config or DEFAULT_CONFIG
        try:
            result = subprocess.run(
                [ccls_executable, "--version"],
                capture_output=True, text=True, timeout=cfg.version_check_timeout
            )
            version_output = result.stdout.strip() or result.stderr.strip()
            # Extract version number (e.g., "ccls version 0.20240505-...")
            match = re.search(r"(\d+)\.(\d+)", version_output)
            if match:
                major, minor = int(match.group(1)), int(match.group(2))
                if (major, minor) >= cfg.min_ccls_version:
                    return True, version_output
                return False, f"ccls {major}.{minor} is below minimum {cfg.min_ccls_version[0]}.{cfg.min_ccls_version[1]}"
            return True, version_output  # Can't parse version, assume OK
        except FileNotFoundError:
            return False, "ccls executable not found"
        except Exception as e:
            return False, f"Error checking ccls version: {e}"

    def generate_ccls_config(self, project_root: str) -> str:
        """
        Generates the .ccls configuration file at the PROJECT ROOT.
        The .ccls file must sit alongside the source code for ccls to detect it.
        Will not overwrite an existing .ccls or compile_commands.json.
        """
        ccls_file_path = os.path.join(project_root, ".ccls")
        compile_commands_path = os.path.join(project_root, "compile_commands.json")

        # Don't overwrite existing build configuration
        if os.path.exists(compile_commands_path):
            logger.info(f"compile_commands.json found, skipping .ccls generation")
            return compile_commands_path
        if os.path.exists(ccls_file_path):
            logger.info(f"Existing .ccls config found at {ccls_file_path}")
            return ccls_file_path

        # Generate config from configured defaults
        ignore_lines = "\n".join(
            f"%ignore {pattern}" for pattern in self.config.ccls_ignore_patterns
        )
        ccls_file_content = (
            f"clang\n"
            f"%c -std={self.config.default_c_standard}\n"
            f"%cpp -std={self.config.default_cpp_standard}\n"
            f"{ignore_lines}\n"
        )

        try:
            with open(ccls_file_path, "w", encoding="utf-8") as f:
                f.write(ccls_file_content)
            logger.info(f"Generated .ccls config at {ccls_file_path}")
            return ccls_file_path
        except IOError as e:
            logger.warning(f"Failed to write .ccls file to project root (permission issue?): {e}")
            return ""

    def run_indexing(
        self,
        project_root: str,
        output_dir: str,
        unique_project_prefix: Optional[str] = None,
    ) -> bool:
        """
        Runs the ccls indexer.

        :param project_root: Absolute path to the source code.
        :param output_dir: Directory where the .ccls-cache will be stored.
        :param unique_project_prefix: Optional identifier for the project (used for logging).
        """
        # Ensure absolute paths
        abs_project_root = os.path.abspath(project_root)
        abs_output_dir = os.path.abspath(output_dir)
        project_label = unique_project_prefix or os.path.basename(abs_project_root)

        # 1. Validate project root
        if not os.path.isdir(abs_project_root):
            logger.error(f"Project root does not exist: {abs_project_root}")
            return False

        # 2. Setup Cache Directory
        ccls_cache_dir = os.path.join(abs_output_dir, ".ccls-cache")
        os.makedirs(ccls_cache_dir, exist_ok=True)

        # 3. Generate Config at Source Root
        self.generate_ccls_config(abs_project_root)

        # 4. Configure CCLS Cache Location
        init_config = json.dumps({
            "cache": {
                "directory": ccls_cache_dir,
                "retainInMemory": 0,
            }
        })

        # 5. Check Executable and Version
        ccls_executable = self.env.get("CCLS_BIN_PATH", self.config.ccls_executable)
        if not shutil.which(ccls_executable):
            logger.error(f"CCLS executable '{ccls_executable}' not found in PATH.")
            return False

        is_valid, version_info = self.check_ccls_version(ccls_executable, config=self.config)
        if not is_valid:
            logger.error(f"CCLS version check failed: {version_info}")
            return False
        logger.info(f"CCLS version: {version_info}")

        # 6. Run indexing
        ccls_cmd = [ccls_executable, "--index", abs_project_root, "--init", init_config]

        logger.info(f"Starting CCLS indexing for [{project_label}] at {abs_project_root}...")
        logger.debug(f"Command: {' '.join(ccls_cmd)}")

        try:
            result = subprocess.run(
                ccls_cmd,
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.indexing_timeout_seconds,
            )

            if result.stdout:
                logger.debug(f"CCLS stdout: {result.stdout[:self.config.log_output_truncation]}")
            logger.info(f"CCLS Ingestion/Indexing Successful for [{project_label}]")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"CCLS indexing failed with exit code {e.returncode}")
            if e.stderr:
                logger.error(f"STDERR: {e.stderr[:self.config.log_error_truncation]}")
            if e.stdout:
                logger.error(f"STDOUT: {e.stdout[:self.config.log_error_truncation]}")
            return False

        except subprocess.TimeoutExpired:
            logger.error(f"CCLS indexing timed out after {self.config.indexing_timeout_seconds} seconds.")
            return False

        except Exception as e:
            logger.exception(f"Unexpected error during CCLS indexing: {e}")
            return False