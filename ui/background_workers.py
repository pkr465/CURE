"""
background_workers.py

CURE — Codebase Update & Refactor Engine
Thread-based background runners for analysis and fixer workflows
with Queue-based log capture for real-time UI streaming.

Author: Pavan R
"""

import logging
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Log capture handler — intercepts logging output and pushes to a Queue
# ═══════════════════════════════════════════════════════════════════════════════

class LogCaptureHandler(logging.Handler):
    """
    Custom logging handler that redirects log records to a Queue
    for real-time display in the Streamlit UI.
    """

    def __init__(self, log_queue: Queue, phase_tracker: Optional[Dict] = None):
        super().__init__()
        self.log_queue = log_queue
        self.phase_tracker = phase_tracker or {}
        self._current_phase = 0

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            level = record.levelname
            ts = datetime.now().strftime("%H:%M:%S")

            # Detect phase transitions from rich console output patterns
            phase = self._detect_phase(msg)
            if phase and phase != self._current_phase:
                self._current_phase = phase
                if self.phase_tracker is not None:
                    # Mark previous phases as completed
                    for p in range(1, phase):
                        self.phase_tracker[p] = "completed"
                    self.phase_tracker[phase] = "in_progress"

            self.log_queue.put({
                "phase": self._current_phase,
                "message": msg,
                "level": level,
                "timestamp": ts,
            })
        except Exception:
            self.handleError(record)

    def _detect_phase(self, msg: str) -> Optional[int]:
        """Detect pipeline phase from log message content."""
        phase_keywords = {
            1: ["file discovery", "discovering files", "file cache", "scanning"],
            2: ["batch analysis", "analyzing batch", "batch processing", "analyzer"],
            3: ["dependency graph", "building graph", "dependency analysis"],
            4: ["health metrics", "calculating metrics", "health score"],
            5: ["llm enrichment", "llm analysis", "llm call", "semantic analysis"],
            6: ["report generation", "generating report", "excel", "saving report"],
            7: ["visualization", "mermaid", "diagram", "summary"],
        }
        msg_lower = msg.lower()
        for phase_num, keywords in phase_keywords.items():
            if any(kw in msg_lower for kw in keywords):
                return phase_num
        return None


class ConsoleCaptureHandler:
    """
    Intercepts rich.console.Console output by wrapping the file object.
    Pushes captured lines to a Queue for UI display.
    """

    def __init__(self, log_queue: Queue, original_stdout=None):
        self.log_queue = log_queue
        self.original_stdout = original_stdout or sys.stdout
        self._buffer = ""

    def write(self, text: str):
        # Pass through to original stdout
        self.original_stdout.write(text)
        # Capture non-empty lines
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if line:
                # Strip ANSI escape codes for clean display
                clean = _strip_ansi(line)
                if clean:
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.log_queue.put({
                        "phase": 0,
                        "message": clean,
                        "level": "INFO",
                        "timestamp": ts,
                    })

    def flush(self):
        self.original_stdout.flush()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase definitions
# ═══════════════════════════════════════════════════════════════════════════════

ANALYSIS_PHASES = {
    1: "File Discovery & Caching",
    2: "Batch Analysis (9 Analyzers)",
    3: "Dependency Graph Building",
    4: "Health Metrics Calculation",
    5: "LLM Enrichment",
    6: "Report Generation",
    7: "Visualization & Summary",
}

FIXER_PHASES = {
    1: "Parsing Directives",
    2: "Applying Fixes",
    3: "Validating Integrity",
    4: "Generating Audit Report",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Analysis background runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_analysis_background(
    config: Dict[str, Any],
    log_queue: Queue,
    result_store: Dict[str, Any],
) -> None:
    """
    Run codebase analysis in a background thread.

    Args:
        config: Analysis configuration dict with keys:
            - codebase_path (str): Path to codebase
            - output_dir (str): Output directory
            - analysis_mode (str): "llm_exclusive" or "static"
            - dependency_granularity (str): "File", "Module", "Package"
            - use_llm (bool): Enable LLM enrichment
            - enable_adapters (bool): Enable deep static adapters
            - max_files (int): Max files to analyze
            - batch_size (int): Batch size
            - llm_model (str, optional): LLM model override
            - exclude_dirs (list): Directories to exclude
        log_queue: Queue to push log messages to
        result_store: Shared dict to store results when complete
    """
    phase_statuses = {i: "pending" for i in range(1, 8)}
    result_store["phase_statuses"] = phase_statuses

    # Install log capture handler
    log_handler = LogCaptureHandler(log_queue, phase_tracker=phase_statuses)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    # Also capture stdout for rich console output
    console_capture = ConsoleCaptureHandler(log_queue)
    original_stdout = sys.stdout
    sys.stdout = console_capture

    try:
        codebase_path = config.get("codebase_path", "./codebase")
        output_dir = config.get("output_dir", "./out")
        os.makedirs(output_dir, exist_ok=True)

        analysis_mode = config.get("analysis_mode", "llm_exclusive")

        _push_log(log_queue, f"Starting {analysis_mode} analysis on: {codebase_path}")

        # Initialize GlobalConfig if available
        global_config = None
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            global_config = GlobalConfig()
        except Exception:
            pass

        # Initialize LLMTools (router auto-selects provider from config)
        llm_tools = None
        if config.get("use_llm", True):
            try:
                from utils.common.llm_tools import LLMTools
                llm_model = config.get("llm_model")
                if llm_model:
                    llm_tools = LLMTools(model=llm_model)
                else:
                    # Always use LLMTools() which internally calls LLMConfig.from_env()
                    # Do NOT pass GlobalConfig directly — LLMTools expects LLMConfig.
                    llm_tools = LLMTools()
                _push_log(log_queue, f"LLM initialized: {llm_tools.get_provider_info()}")
            except Exception as e:
                _push_log(log_queue, f"LLM init failed: {e}", level="WARNING")

        # Initialize HITL context if available
        hitl_context = None
        try:
            from hitl import HITLContext, HITLConfig, HITL_AVAILABLE
            if HITL_AVAILABLE:
                hitl_config = HITLConfig()
                hitl_context = HITLContext(config=hitl_config, llm_tools=llm_tools)
                _push_log(log_queue, "HITL context initialized")
        except Exception:
            pass

        if analysis_mode == "llm_exclusive":
            # LLM-exclusive mode: CodebaseLLMAgent
            _push_log(log_queue, "Mode: LLM-Exclusive Code Review")
            from agents.codebase_llm_agent import CodebaseLLMAgent

            agent = CodebaseLLMAgent(
                codebase_path=codebase_path,
                output_dir=output_dir,
                config=global_config,
                llm_tools=llm_tools,
                exclude_dirs=config.get("exclude_dirs", []),
                max_files=config.get("max_files", 2000),
                use_ccls=config.get("use_ccls", False),
                file_to_fix=config.get("file_to_fix"),
                hitl_context=hitl_context,
            )

            # Run analysis
            report_path = agent.run_analysis(
                output_filename="detailed_code_review.xlsx",
            )

            # Store results
            result_store["analysis_results"] = getattr(agent, "results", [])
            result_store["report_path"] = report_path
            result_store["analysis_mode"] = "llm_exclusive"
            result_store["status"] = "success"
            _push_log(log_queue, f"LLM analysis complete. Report: {report_path}")

        else:
            # Standard mode: StaticAnalyzerAgent
            _push_log(log_queue, "Mode: Static Analysis Pipeline")
            from agents.codebase_static_agent import StaticAnalyzerAgent

            agent = StaticAnalyzerAgent(
                codebase_path=codebase_path,
                output_dir=os.path.join(output_dir, "parseddata"),
                config=global_config,
                llm_tools=llm_tools,
                max_files=config.get("max_files", 2000),
                exclude_dirs=config.get("exclude_dirs", []),
                batch_size=config.get("batch_size", 25),
                memory_limit_mb=config.get("memory_limit", 3000),
                enable_llm=config.get("use_llm", False),
                enable_adapters=config.get("enable_adapters", False),
                verbose=True,
                hitl_context=hitl_context,
            )

            results = agent.run_analysis()

            # Store results
            result_store["analysis_results"] = results.get("file_cache", [])
            result_store["analysis_metrics"] = results.get("health_metrics", {})
            result_store["health_report_path"] = results.get("health_report_path")
            result_store["analysis_mode"] = "static"
            result_store["status"] = "success"
            _push_log(log_queue, "Static analysis pipeline complete.")

        # Mark all phases completed
        for p in phase_statuses:
            phase_statuses[p] = "completed"
        result_store["phase_statuses"] = phase_statuses

    except Exception as e:
        _push_log(log_queue, f"Analysis failed: {e}", level="ERROR")
        result_store["status"] = f"error: {e}"
        logger.error("Background analysis failed", exc_info=True)

    finally:
        # Restore stdout and remove handler
        sys.stdout = original_stdout
        root_logger.removeHandler(log_handler)
        _push_log(log_queue, "__DONE__")


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixer background runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_fixer_background(
    config: Dict[str, Any],
    log_queue: Queue,
    result_store: Dict[str, Any],
) -> None:
    """
    Run the fixer workflow in a background thread.

    Args:
        config: Fixer configuration dict with keys:
            - directives_path (str): Path to JSONL directives
            - codebase_path (str): Path to codebase
            - output_dir (str): Output directory
            - dry_run (bool): Simulate fixes without writing
            - llm_model (str, optional): LLM model override
        log_queue: Queue to push log messages to
        result_store: Shared dict to store results when complete
    """
    phase_statuses = {i: "pending" for i in range(1, 5)}
    result_store["fixer_phase_statuses"] = phase_statuses

    # Install log capture
    log_handler = LogCaptureHandler(log_queue, phase_tracker=phase_statuses)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    original_stdout = sys.stdout
    console_capture = ConsoleCaptureHandler(log_queue)
    sys.stdout = console_capture

    try:
        directives_path = config.get("directives_path")
        codebase_path = config.get("codebase_path", "./codebase")
        output_dir = config.get("output_dir", "./out")
        dry_run = config.get("dry_run", False)

        _push_log(log_queue, f"Starting fixer workflow on: {codebase_path}")
        _push_log(log_queue, f"Directives: {directives_path}")
        _push_log(log_queue, f"Dry run: {dry_run}")

        # Initialize config and LLM tools
        global_config = None
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            global_config = GlobalConfig()
        except Exception:
            pass

        llm_tools = None
        try:
            from utils.common.llm_tools import LLMTools
            llm_model = config.get("llm_model")
            llm_tools = LLMTools(model=llm_model) if llm_model else LLMTools()
        except Exception as e:
            _push_log(log_queue, f"LLM init failed: {e}", level="WARNING")

        # Phase 1: Parse directives
        phase_statuses[1] = "in_progress"
        _push_log(log_queue, "Phase 1: Loading directives...")

        from agents.codebase_fixer_agent import CodebaseFixerAgent

        backup_dir = os.path.join(output_dir, "shelved_backups")
        os.makedirs(backup_dir, exist_ok=True)

        agent = CodebaseFixerAgent(
            codebase_root=codebase_path,
            directives_file=directives_path,
            backup_dir=backup_dir,
            output_dir=output_dir,
            config=global_config,
            llm_tools=llm_tools,
            dry_run=dry_run,
            verbose=True,
        )

        phase_statuses[1] = "completed"

        # Phase 2-3: Apply fixes (the agent handles these internally)
        phase_statuses[2] = "in_progress"
        _push_log(log_queue, "Phase 2: Applying fixes...")

        fixer_result = agent.run_agent(
            report_filename="final_execution_audit.xlsx",
        )

        phase_statuses[2] = "completed"
        phase_statuses[3] = "completed"

        # Phase 4: Report
        phase_statuses[4] = "in_progress"
        _push_log(log_queue, "Phase 4: Generating audit report...")

        result_store["fixer_results"] = fixer_result
        result_store["fixer_status"] = "success"
        result_store["audit_report_path"] = fixer_result.get("report_path", "")

        phase_statuses[4] = "completed"
        _push_log(log_queue, "Fixer workflow complete.")

    except Exception as e:
        _push_log(log_queue, f"Fixer workflow failed: {e}", level="ERROR")
        result_store["fixer_status"] = f"error: {e}"
        logger.error("Background fixer failed", exc_info=True)

    finally:
        sys.stdout = original_stdout
        root_logger.removeHandler(log_handler)
        _push_log(log_queue, "__DONE__")


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _push_log(
    log_queue: Queue,
    message: str,
    level: str = "INFO",
    phase: int = 0,
) -> None:
    """Push a log entry to the queue."""
    ts = datetime.now().strftime("%H:%M:%S")
    log_queue.put({
        "phase": phase,
        "message": message,
        "level": level,
        "timestamp": ts,
    })
