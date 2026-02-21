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

# ── Silence noisy third-party loggers ────────────────────────────────────────
# http.client dumps raw HTTP bytes (STREAM b'IHDR', etc.) at DEBUG level;
# urllib3/httpcore/httpx log every connection and retry at DEBUG.
# Force these to WARNING so they only appear in debug.log via the root logger
# if someone explicitly lowers their level.
for _noisy in (
    "http.client", "urllib3", "urllib3.connectionpool",
    "httpcore", "httpx", "httpcore.http11", "httpcore.connection",
    "requests", "openai", "anthropic", "PIL", "PIL.PngImagePlugin",
    "matplotlib", "chardet", "charset_normalizer",
    "dependency_builder", "DependencyFetcher",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Telemetry singleton (lazy init) ──────────────────────────────────────────
_telemetry_service = None


def _get_telemetry():
    """Lazy-load the TelemetryService singleton."""
    global _telemetry_service
    if _telemetry_service is not None:
        return _telemetry_service
    try:
        from utils.parsers.global_config_parser import GlobalConfig
        gc = GlobalConfig()
        telemetry_enabled = gc.get_bool("telemetry.enable", True)
        if not telemetry_enabled:
            from db.telemetry_service import TelemetryService
            _telemetry_service = TelemetryService(enabled=False)
            return _telemetry_service
        conn_str = gc.get("POSTGRES_CONNECTION")
        if conn_str:
            from db.telemetry_service import TelemetryService
            _telemetry_service = TelemetryService(connection_string=conn_str)
            return _telemetry_service
    except Exception as exc:
        logger.debug("Telemetry not available: %s", exc)
    # Return a disabled stub
    try:
        from db.telemetry_service import TelemetryService
        _telemetry_service = TelemetryService(enabled=False)
    except ImportError:
        _telemetry_service = None
    return _telemetry_service


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
            1: ["file discovery", "discovering files", "file cache", "scanning",
                "found", "files to analyze", "matching files"],
            2: ["batch analysis", "analyzing batch", "batch processing", "analyzer",
                "analyzing:", "processing chunk", "analysis (run id"],
            3: ["dependency graph", "building graph", "dependency analysis",
                "dependency fetch", "ccls"],
            4: ["health metrics", "calculating metrics", "health score",
                "critical issues"],
            5: ["llm enrichment", "llm analysis", "llm call", "semantic analysis",
                "llm initialized", "llm-exclusive"],
            6: ["report generation", "generating report", "excel", "saving report",
                "report saved", "success!"],
            7: ["visualization", "mermaid", "diagram", "summary",
                "vector db", "vector-ready", "ingestion complete"],
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
    When debug_mode is False, stdout passthrough is suppressed (output only
    goes to debug.log via the logging file handler).
    """

    def __init__(self, log_queue: Queue, original_stdout=None, debug_mode: bool = False):
        self.log_queue = log_queue
        self.original_stdout = original_stdout or sys.stdout
        self.debug_mode = debug_mode
        self._buffer = ""

    def write(self, text: str):
        # Only pass through to original stdout in debug mode
        if self.debug_mode:
            self.original_stdout.write(text)
        # Capture non-empty lines for UI only in debug mode
        if self.debug_mode:
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


def _setup_debug_file_handler(output_dir: str) -> Optional[logging.FileHandler]:
    """
    Create a DEBUG-level FileHandler writing to {output_dir}/debug.log.

    Returns the handler (caller must add/remove it from the root logger),
    or None if the file cannot be created.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, "debug.log")
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ))
        return fh
    except Exception as exc:
        logger.debug("Could not create debug.log: %s", exc)
        return None


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

    # Remove any pre-existing StreamHandlers (e.g. from logging.basicConfig in main.py)
    # so debug messages don't leak to the terminal console
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root_logger.removeHandler(h)

    # Install log capture handler — UI console only sees WARNING+
    log_handler = LogCaptureHandler(log_queue, phase_tracker=phase_statuses)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    log_handler.setLevel(logging.WARNING)
    root_logger.addHandler(log_handler)

    # Debug file handler — writes all DEBUG+ logs to {output_dir}/debug.log
    output_dir = config.get("output_dir", "./out")
    debug_fh = _setup_debug_file_handler(output_dir)
    if debug_fh:
        debug_fh.setLevel(logging.DEBUG)
        root_logger.addHandler(debug_fh)
        # Root must be DEBUG so file handler receives all messages
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    # Also capture stdout for rich console output
    console_capture = ConsoleCaptureHandler(log_queue, debug_mode=config.get("debug_mode", False))
    original_stdout = sys.stdout
    sys.stdout = console_capture

    start_time = time.time()
    telemetry = _get_telemetry()
    run_id = ""

    try:
        codebase_path = config.get("codebase_path", "./codebase")
        output_dir = config.get("output_dir", "./out")
        os.makedirs(output_dir, exist_ok=True)

        analysis_mode = config.get("analysis_mode", "llm_exclusive")

        # Start telemetry run
        if telemetry:
            run_id = telemetry.start_run(
                mode="analysis",
                codebase_path=codebase_path,
                llm_model=config.get("llm_model", ""),
                use_ccls=config.get("use_ccls", False),
                use_hitl=bool(config.get("enable_hitl", False)),
                metadata={"analysis_mode": analysis_mode},
            )
            result_store["telemetry_run_id"] = run_id

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

        # Initialize HITL context if enabled
        hitl_context = None
        if config.get("enable_hitl", False):
            try:
                from hitl import HITLContext, HITLConfig, HITL_AVAILABLE
                if HITL_AVAILABLE:
                    if global_config:
                        hitl_config = HITLConfig.from_global_config(global_config)
                    else:
                        hitl_config = HITLConfig()
                    hitl_context = HITLContext(config=hitl_config, llm_tools=llm_tools)
                    _push_log(log_queue, "HITL context initialized (PostgreSQL)")
            except Exception as e:
                _push_log(log_queue, f"HITL init failed: {e}", level="WARNING")

        if analysis_mode == "llm_exclusive":
            # LLM-exclusive mode: CodebaseLLMAgent
            _push_log(log_queue, "Mode: LLM-Exclusive Code Review")
            from agents.codebase_llm_agent import CodebaseLLMAgent

            # Phase 1: File Discovery
            phase_statuses[1] = "in_progress"
            result_store["phase_statuses"] = phase_statuses
            _push_log(log_queue, "Phase 1: Discovering files in codebase...")

            agent = CodebaseLLMAgent(
                codebase_path=codebase_path,
                output_dir=output_dir,
                config=global_config,
                llm_tools=llm_tools,
                exclude_dirs=config.get("exclude_dirs", []),
                exclude_globs=config.get("exclude_globs", []),
                max_files=config.get("max_files", 2000),
                use_ccls=config.get("use_ccls", False),
                file_to_fix=config.get("file_to_fix"),
                hitl_context=hitl_context,
                custom_constraints=config.get("custom_constraints", []),
            )

            phase_statuses[1] = "completed"

            # Phase 2-4: LLM Analysis (bulk of the work)
            phase_statuses[2] = "in_progress"
            result_store["phase_statuses"] = phase_statuses
            _push_log(log_queue, "Phase 2-4: Running LLM analysis...")

            # Run LLM analysis first (populates agent.results)
            report_path = agent.run_analysis(
                output_filename="detailed_code_review.xlsx",
            )

            phase_statuses[2] = "completed"
            phase_statuses[3] = "completed"
            phase_statuses[4] = "completed"
            result_store["phase_statuses"] = phase_statuses

            # Run deep static adapters if enabled (after LLM analysis)
            adapter_results = None
            if config.get("enable_adapters", False):
                phase_statuses[5] = "in_progress"
                result_store["phase_statuses"] = phase_statuses
                _push_log(log_queue, "Phase 5: Running deep static analysis adapters...")
                try:
                    from agents.core.metrics_calculator import MetricsCalculator
                    mc = MetricsCalculator(
                        codebase_path=codebase_path,
                        output_dir=output_dir,
                        enable_adapters=True,
                    )
                    # Build file_cache from codebase files (not from LLM results)
                    # NOTE: adapters expect "source" key for file contents
                    file_cache = []
                    gathered = agent._gather_files()
                    for fpath in gathered:
                        try:
                            rel = os.path.relpath(str(fpath), codebase_path)
                        except ValueError:
                            rel = str(fpath)
                        source = ""
                        try:
                            with open(str(fpath), "r", encoding="utf-8", errors="replace") as f:
                                source = f.read()
                        except Exception:
                            pass
                        file_cache.append({
                            "file_path": str(fpath),
                            "file_relative_path": rel,
                            "file_name": os.path.basename(str(fpath)),
                            "source": source,
                        })
                    adapter_results = mc._run_adapters(file_cache, {})
                    # Log per-adapter results for diagnostics
                    for aname, ares in adapter_results.items():
                        avail = ares.get("tool_available", False)
                        dcount = len(ares.get("details", []))
                        grade = ares.get("grade", "?")
                        _push_log(log_queue, f"  Adapter {aname}: grade={grade} details={dcount} tool_available={avail}")
                    _push_log(log_queue, f"Deep adapters complete: {list(adapter_results.keys())}")

                    # Count adapters that produced detail rows
                    adapters_with_details = sum(
                        1 for r in adapter_results.values() if r.get("details")
                    )
                    _push_log(log_queue, f"Adapters with findings: {adapters_with_details}/{len(adapter_results)}")

                    # Re-generate Excel with adapter sheets appended
                    if adapter_results and report_path:
                        try:
                            agent._generate_excel_report(
                                report_path,
                                adapter_results=adapter_results,
                            )
                            _push_log(log_queue, "Excel report regenerated with adapter sheets.")
                        except Exception as regen_err:
                            _push_log(log_queue, f"Excel regen with adapters failed: {regen_err}", level="WARNING")
                except Exception as adp_err:
                    _push_log(log_queue, f"Deep adapters failed: {adp_err}", level="WARNING")
                    adapter_results = None
                phase_statuses[5] = "completed"
            else:
                phase_statuses[5] = "completed"

            # Phase 6: Report Generation
            phase_statuses[6] = "in_progress"
            result_store["phase_statuses"] = phase_statuses

            # Store results
            result_store["analysis_results"] = getattr(agent, "results", [])
            result_store["report_path"] = report_path
            result_store["adapter_results"] = adapter_results
            result_store["analysis_mode"] = "llm_exclusive"
            result_store["status"] = "success"
            _push_log(log_queue, f"LLM analysis complete. Report: {report_path}")

            phase_statuses[6] = "completed"

            # Phase 7: Vector DB ingestion (if enabled)
            phase_statuses[7] = "in_progress"
            result_store["phase_statuses"] = phase_statuses

            if config.get("enable_vector_db") and getattr(agent, "results", None):
                try:
                    import os as _os
                    parseddata_dir = _os.path.join(output_dir, "parseddata")
                    _os.makedirs(parseddata_dir, exist_ok=True)
                    ndjson_out = _os.path.join(parseddata_dir, "llm_review_vector.ndjson")
                    agent._write_vector_ndjson(ndjson_out)
                    _push_log(log_queue, f"Vector-ready NDJSON written: {ndjson_out}")

                    from db.vectordb_pipeline import VectorDbPipeline
                    pipeline = VectorDbPipeline()
                    pipeline.run()
                    _push_log(log_queue, "Vector DB ingestion complete — chat is now available")
                except Exception as vec_err:
                    _push_log(log_queue, f"Vector DB ingestion skipped: {vec_err}", level="WARNING")

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
                exclude_globs=config.get("exclude_globs", []),
                batch_size=config.get("batch_size", 25),
                memory_limit_mb=config.get("memory_limit", 3000),
                enable_llm=config.get("use_llm", False),
                enable_adapters=config.get("enable_adapters", False),
                verbose=config.get("debug_mode", False),
                hitl_context=hitl_context,
            )

            results = agent.run_analysis()

            # Store results
            result_store["analysis_results"] = results.get("file_cache", [])
            result_store["analysis_metrics"] = results.get("health_metrics", {})
            result_store["health_report_path"] = results.get("health_report_path")
            result_store["adapter_results"] = results.get("adapter_results", {})
            result_store["analysis_mode"] = "static"
            result_store["status"] = "success"
            _push_log(log_queue, "Static analysis pipeline complete.")

        # Mark all phases completed
        for p in phase_statuses:
            phase_statuses[p] = "completed"
        result_store["phase_statuses"] = phase_statuses

        # Finalize telemetry
        if telemetry and run_id:
            duration = time.time() - start_time
            results = result_store.get("analysis_results", [])
            issues_total = len(results) if isinstance(results, list) else 0
            telemetry.finish_run(
                run_id=run_id,
                status="completed",
                files_analyzed=config.get("max_files", 0),
                issues_total=issues_total,
                duration_seconds=duration,
                metadata={"use_ccls": config.get("use_ccls", False)},
            )

    except Exception as e:
        _push_log(log_queue, f"Analysis failed: {e}", level="ERROR")
        result_store["status"] = f"error: {e}"
        logger.error("Background analysis failed", exc_info=True)
        if telemetry and run_id:
            telemetry.finish_run(
                run_id=run_id,
                status="failed",
                duration_seconds=time.time() - start_time,
                metadata={"error": str(e)},
            )

    finally:
        # Restore stdout and remove handlers
        sys.stdout = original_stdout
        root_logger.removeHandler(log_handler)
        if debug_fh:
            debug_fh.close()
            root_logger.removeHandler(debug_fh)
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

    # Remove any pre-existing StreamHandlers (e.g. from logging.basicConfig in main.py)
    # so debug messages don't leak to the terminal console
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root_logger.removeHandler(h)

    # Install log capture — UI console only sees WARNING+
    log_handler = LogCaptureHandler(log_queue, phase_tracker=phase_statuses)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    log_handler.setLevel(logging.WARNING)
    root_logger.addHandler(log_handler)

    # Debug file handler
    output_dir = config.get("output_dir", "./out")
    debug_fh = _setup_debug_file_handler(output_dir)
    if debug_fh:
        debug_fh.setLevel(logging.DEBUG)
        root_logger.addHandler(debug_fh)
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    original_stdout = sys.stdout
    console_capture = ConsoleCaptureHandler(log_queue, debug_mode=config.get("debug_mode", False))
    sys.stdout = console_capture

    start_time = time.time()
    telemetry = _get_telemetry()
    run_id = ""

    try:
        directives_path = config.get("directives_path")
        codebase_path = config.get("codebase_path", "./codebase")
        output_dir = config.get("output_dir", "./out")
        dry_run = config.get("dry_run", False)

        # Start telemetry run
        if telemetry:
            run_id = telemetry.start_run(
                mode="fixer",
                codebase_path=codebase_path,
                llm_model=config.get("llm_model", ""),
                metadata={"dry_run": dry_run, "directives": directives_path},
            )
            result_store["telemetry_run_id"] = run_id

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
            verbose=config.get("debug_mode", False),
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

        # Finalize telemetry
        if telemetry and run_id:
            duration = time.time() - start_time
            fr = fixer_result or {}
            telemetry.finish_run(
                run_id=run_id,
                status="completed",
                issues_fixed=fr.get("fixed_count", 0),
                issues_skipped=fr.get("skipped_count", 0),
                issues_failed=fr.get("failed_count", 0),
                duration_seconds=duration,
            )

    except Exception as e:
        _push_log(log_queue, f"Fixer workflow failed: {e}", level="ERROR")
        result_store["fixer_status"] = f"error: {e}"
        logger.error("Background fixer failed", exc_info=True)
        if telemetry and run_id:
            telemetry.finish_run(
                run_id=run_id,
                status="failed",
                duration_seconds=time.time() - start_time,
                metadata={"error": str(e)},
            )

    finally:
        sys.stdout = original_stdout
        root_logger.removeHandler(log_handler)
        if debug_fh:
            debug_fh.close()
            root_logger.removeHandler(debug_fh)
        _push_log(log_queue, "__DONE__")


# ═══════════════════════════════════════════════════════════════════════════════
#  Patch analysis background runner
# ═══════════════════════════════════════════════════════════════════════════════

PATCH_PHASES = {
    1: "Reading Source & Patch",
    2: "Parsing Unified Diff",
    3: "Applying Patch",
    4: "LLM Analysis (Original vs Patched)",
    5: "Static Analysis (Adapters)",
    6: "Diffing Findings",
    7: "Report Generation",
}


def run_patch_analysis_background(
    config: Dict[str, Any],
    log_queue: Queue,
    result_store: Dict[str, Any],
) -> None:
    """
    Run patch analysis in a background thread.

    Args:
        config: Patch analysis configuration dict with keys:
            - file_path (str): Path to original source file
            - patch_file (str): Path to .patch/.diff file
            - modified_file (str, optional): Path to already-modified file
            - output_dir (str): Output directory
            - enable_adapters (bool): Enable deep static adapters
        log_queue: Queue to push log messages to
        result_store: Shared dict to store results when complete
    """
    phase_statuses = {i: "pending" for i in range(1, 8)}
    result_store["phase_statuses"] = phase_statuses

    # Remove any pre-existing StreamHandlers (e.g. from logging.basicConfig in main.py)
    # so debug messages don't leak to the terminal console
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root_logger.removeHandler(h)

    # Install log capture — UI console only sees WARNING+
    log_handler = LogCaptureHandler(log_queue, phase_tracker=phase_statuses)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    log_handler.setLevel(logging.WARNING)
    root_logger.addHandler(log_handler)

    # Debug file handler
    output_dir = config.get("output_dir", "./out")
    debug_fh = _setup_debug_file_handler(output_dir)
    if debug_fh:
        debug_fh.setLevel(logging.DEBUG)
        root_logger.addHandler(debug_fh)
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    original_stdout = sys.stdout
    console_capture = ConsoleCaptureHandler(log_queue, debug_mode=config.get("debug_mode", False))
    sys.stdout = console_capture

    start_time = time.time()
    telemetry = _get_telemetry()
    run_id = ""

    try:
        file_path = config.get("file_path", "")
        patch_file = config.get("patch_file", "")
        modified_file = config.get("modified_file", "")
        output_dir = config.get("output_dir", "./out")
        enable_adapters = config.get("enable_adapters", False)

        os.makedirs(output_dir, exist_ok=True)

        # Start telemetry run
        if telemetry:
            run_id = telemetry.start_run(
                mode="patch",
                codebase_path=file_path,
                metadata={"patch_file": patch_file, "modified_file": modified_file},
            )
            result_store["telemetry_run_id"] = run_id

        _push_log(log_queue, f"Starting patch analysis")
        _push_log(log_queue, f"  Original file: {file_path}")
        _push_log(log_queue, f"  Patch file: {patch_file}")
        if modified_file:
            _push_log(log_queue, f"  Modified file: {modified_file}")

        # If a modified file is provided but no patch file, generate a diff
        if modified_file and not patch_file:
            import subprocess as sp
            _push_log(log_queue, "Generating diff from original and modified files...")
            diff_path = os.path.join(output_dir, "_generated.patch")
            try:
                result = sp.run(
                    ["diff", "-u", file_path, modified_file],
                    capture_output=True, text=True, timeout=30,
                )
                with open(diff_path, "w", encoding="utf-8") as f:
                    f.write(result.stdout)
                patch_file = diff_path
                _push_log(log_queue, f"  Generated patch: {diff_path}")
            except Exception as e:
                _push_log(log_queue, f"Failed to generate diff: {e}", level="ERROR")
                result_store["status"] = f"error: {e}"
                return

        # Initialize GlobalConfig
        global_config = None
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            global_config = GlobalConfig()
        except Exception:
            pass

        # Initialize LLM
        llm_tools = None
        try:
            from utils.common.llm_tools import LLMTools
            llm_tools = LLMTools()
            _push_log(log_queue, f"LLM initialized: {llm_tools.get_provider_info()}")
        except Exception as e:
            _push_log(log_queue, f"LLM init failed (continuing without): {e}", level="WARNING")

        # Initialize HITL
        hitl_context = None
        try:
            from hitl import HITLContext, HITLConfig, HITL_AVAILABLE
            if HITL_AVAILABLE:
                hitl_config = HITLConfig()
                hitl_context = HITLContext(config=hitl_config, llm_tools=llm_tools)
        except Exception:
            pass

        # Run patch analysis
        from agents.codebase_patch_agent import CodebasePatchAgent

        agent = CodebasePatchAgent(
            file_path=file_path,
            patch_file=patch_file,
            output_dir=output_dir,
            config=global_config,
            llm_tools=llm_tools,
            hitl_context=hitl_context,
            enable_adapters=enable_adapters,
            verbose=config.get("debug_mode", False),
            exclude_dirs=config.get("exclude_dirs", []),
            exclude_globs=config.get("exclude_globs", []),
            custom_constraints=config.get("custom_constraints", []),
        )

        excel_path = os.path.join(output_dir, "detailed_code_review.xlsx")
        patch_result = agent.run_analysis(excel_path=excel_path)

        # Store results
        result_store["patch_result"] = patch_result
        result_store["report_path"] = patch_result.get("excel_path", "")
        result_store["analysis_results"] = patch_result.get("findings", [])
        result_store["analysis_mode"] = "patch"
        result_store["status"] = patch_result.get("status", "success")

        _push_log(
            log_queue,
            f"Patch analysis complete: "
            f"{patch_result.get('new_issue_count', 0)} new issues found, "
            f"{patch_result.get('patched_issue_count', 0)} total in patched code"
        )

        # Mark all phases completed
        for p in phase_statuses:
            phase_statuses[p] = "completed"
        result_store["phase_statuses"] = phase_statuses

        # Finalize telemetry
        if telemetry and run_id:
            pr = patch_result or {}
            telemetry.finish_run(
                run_id=run_id,
                status="completed",
                files_analyzed=1,
                issues_total=pr.get("new_issue_count", 0),
                duration_seconds=time.time() - start_time,
            )

    except Exception as e:
        _push_log(log_queue, f"Patch analysis failed: {e}", level="ERROR")
        result_store["status"] = f"error: {e}"
        logger.error("Background patch analysis failed", exc_info=True)
        if telemetry and run_id:
            telemetry.finish_run(
                run_id=run_id,
                status="failed",
                duration_seconds=time.time() - start_time,
                metadata={"error": str(e)},
            )

    finally:
        sys.stdout = original_stdout
        root_logger.removeHandler(log_handler)
        if debug_fh:
            debug_fh.close()
            root_logger.removeHandler(debug_fh)
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
