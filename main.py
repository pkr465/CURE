#!/usr/bin/env python
"""
main.py

Main entry point and multi-agent workflow for C/C++ codebase health analysis,
flattening to embedding-ready NDJSON, and ingestion into a PostgreSQL vector DB.

Key stages:
1. PostgreSQL setup (optional, if vector DB enabled)
2. Codebase analysis:
   - StaticAnalyzerAgent: unified 7-phase pipeline (optional LLM enrichment)
   - OR CodebaseLLMAgent for strictly Excel/CSV reporting
   - Writes canonical healthreport.json
3. Flatten + NDJSON generation:
   - JsonFlattener -> *_flat.json
   - NDJSONProcessor -> *.ndjson (embedding-ready)
4. Vector DB ingestion via VectorDbPipeline

Authors: Pavan R, ...
"""

import os
import re
import argparse
import logging
import sys
import json
import time
import gc
import psutil
import signal
from typing import Dict, List, Any, Optional
from pathlib import Path

from rich.console import Console

console = Console()

# HTML health report generator
try:
    from agents.parsers.healthreport_generator import run_health_report
    HEALTHREPORT_GENERATOR_AVAILABLE = True
except ImportError as e:
    HEALTHREPORT_GENERATOR_AVAILABLE = False
    console.print(f"[yellow]Warning: healthreport_generator not available: {e}[/yellow]")

# Global config support
try:
    from utils.parsers.global_config_parser import GlobalConfig
    GLOBAL_CONFIG_AVAILABLE = True
except ImportError as e:
    GLOBAL_CONFIG_AVAILABLE = False
    console.print(f"[yellow]Warning: GlobalConfig not available: {e}[/yellow]")

# Env and DB setup
from utils.parsers.env_parser import EnvConfig
from db.postgres_db_setup import PostgresDbSetup

# LLM Tools
try:
    from utils.common.llm_tools import LLMTools
    LLM_TOOLS_AVAILABLE = True
except ImportError as e:
    LLM_TOOLS_AVAILABLE = False
    console.print(f"[yellow]Warning: LLMTools not available: {e}[/yellow]")

# HITL support (optional)
try:
    from hitl import HITLContext, HITLConfig, HITL_AVAILABLE
except ImportError:
    HITLContext = None
    HITLConfig = None
    HITL_AVAILABLE = False

# New flattening & NDJSON tooling
try:
    from db.json_flattner import JsonFlattener
    from db.ndjson_processor import NDJSONProcessor
    from db.ndjson_writer import NDJSONWriter

    FLATTENING_AVAILABLE = True
except ImportError as e:
    FLATTENING_AVAILABLE = False
    console.print(f"[yellow]Warning: Flattening/NDJSON tools not available: {e}[/yellow]")

# Unified Static Analyzer Agent (Health Report focus)
try:
    from agents.codebase_static_agent import StaticAnalyzerAgent
    STATIC_ANALYZER_AVAILABLE = True
except ImportError as e:
    STATIC_ANALYZER_AVAILABLE = False
    console.print(f"[yellow]Warning: StaticAnalyzerAgent not available: {e}[/yellow]")

# Exclusive LLM Agent (Excel/CSV Report focus)
try:
    from agents.codebase_llm_agent import CodebaseLLMAgent
    LLM_EXCLUSIVE_AGENT_AVAILABLE = True
except ImportError as e:
    LLM_EXCLUSIVE_AGENT_AVAILABLE = False
    console.print(f"[yellow]Warning: CodebaseLLMAgent not available: {e}[/yellow]")

# Patch Analysis Agent
try:
    from agents.codebase_patch_agent import CodebasePatchAgent
    PATCH_AGENT_AVAILABLE = True
except ImportError:
    PATCH_AGENT_AVAILABLE = False
    CodebasePatchAgent = None

# Deep static analysis adapters (Lizard, Flawfinder, CCLS)
try:
    from agents.adapters import (
        DeadCodeAdapter,
        ASTComplexityAdapter,
        SecurityAdapter,
        CallGraphAdapter,
        FunctionMetricsAdapter,
        ExcelReportAdapter,
    )
    ADAPTERS_AVAILABLE = True
except ImportError as e:
    ADAPTERS_AVAILABLE = False

# Vector document processor (if still in use for non-health docs)
try:
    from agents.parsers.healthreport_parser import HealthReportParser
    from agents.vector_db.document_processor import VectorDBDocumentProcessor
    VECTOR_PROCESSING_AVAILABLE = True
except ImportError:
    VECTOR_PROCESSING_AVAILABLE = False

# Vector DB pipeline (NDJSON -> PostgresVectorStore)
try:
    from db.vectordb_pipeline import VectorDbPipeline
    VECTOR_DB_PIPELINE_AVAILABLE = True
except ImportError as e:
    VECTOR_DB_PIPELINE_AVAILABLE = False
    console.print(f"[yellow]Warning: VectorDbPipeline not available: {e}[/yellow]")

# LangGraph
from langgraph.graph import StateGraph

# --------- Global shutdown flag ---------
shutdown_requested = False


def signal_handler(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    console.print("\n[yellow]⚠️  Shutdown requested. Finishing current batch...[/yellow]")


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --------- Logging with memory tracking ---------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s - [Memory: %(memory_mb).1fMB]",
    stream=sys.stdout,
    force=True,
)


class MemoryFormatter(logging.Formatter):
    def format(self, record):
        process = psutil.Process()
        record.memory_mb = process.memory_info().rss / 1024 / 1024
        return super().format(record)


for handler in logging.getLogger().handlers:
    handler.setFormatter(MemoryFormatter())

logger = logging.getLogger(__name__)

# Silence noisy third-party loggers that dump raw HTTP bytes at DEBUG level
for _noisy in (
    "http.client", "urllib3", "urllib3.connectionpool",
    "httpcore", "httpx", "httpcore.http11", "httpcore.connection",
    "requests", "openai", "anthropic", "PIL", "PIL.PngImagePlugin",
    "matplotlib", "chardet", "charset_normalizer",
    "dependency_builder", "DependencyFetcher",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def get_memory_usage() -> float:
    process = psutil.Process()
    return process.memory_info().rss / 1024 / 1024


def log_memory_usage(context: str) -> float:
    mem = get_memory_usage()
    logger.info(f"Memory usage at {context}: {mem:.1f}MB")
    if mem > 2000:
        logger.warning(f"High memory usage detected: {mem:.1f}MB")
    return mem


def force_garbage_collection() -> float:
    before = get_memory_usage()
    gc.collect()
    after = get_memory_usage()
    freed = before - after
    if freed > 10:
        logger.info(
            f"Garbage collection freed {freed:.1f}MB "
            f"(was {before:.1f}MB, now {after:.1f}MB)"
        )
    return after


# --------- CLI Argument Parsing ---------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Comprehensive C/C++ Codebase Analysis with Vector Database Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic analysis with LLM agent
  python codebase_analysis.py --codebase-path /path/to/cpp/project --use-llm

  # Exclusive LLM analysis (Direct Excel Report)
  python codebase_analysis.py --codebase-path /path/to/cpp/project --llm-exclusive

  # Exclusive LLM analysis with CCLS dependency services
  python codebase_analysis.py --codebase-path /path/to/cpp/project --llm-exclusive --use-ccls

  # Incremental analysis + vector DB (flattened + NDJSON)
  python codebase_analysis.py --codebase-path /path/to/cpp/project --enable-vector-db

  # Memory-constrained environments
  python codebase_analysis.py --codebase-path /path/to/cpp/project --batch-size 10 --memory-limit 2000
        """,
    )

    # Legacy arguments (kept for compatibility)
    parser.add_argument("inputpath", nargs="?", default=None, help="Input path (legacy, use --codebase-path)")
    parser.add_argument("outputpath", nargs="?", default=None, help="Output path (legacy, use --out-dir)")
    parser.add_argument("--with-pandoc", default=None, help="legacy")
    parser.add_argument("--with-wmf2svg", default=None, help="legacy")
    parser.add_argument("-f", "--format", default=None, help="legacy")
    parser.add_argument("-t", "--toc", dest="toc", action="store_true", help="legacy")
    parser.add_argument("--no-toc", dest="toc", action="store_false")
    parser.set_defaults(toc=None)
    parser.add_argument("--img-dir", default=None, help="legacy")
    parser.add_argument("-H", "--html", action="store_true", default=None, help="legacy")
    parser.add_argument("-k", "--keep-imgdims", action="store_true", default=None)
    parser.add_argument("--no-keep-imgdims", dest="keep_imgdims", action="store_false")
    parser.set_defaults(keep_imgdims=None)
    parser.add_argument("-I", "--recalc-imgdims", action="store_true", default=None)
    parser.add_argument("--no-recalc-imgdims", dest="recalc_imgdims", action="store_false")
    parser.set_defaults(recalc_imgdims=None)
    parser.add_argument("-M", "--recalc-maxdims", type=int, default=None)

    # Core C/C++ analysis arguments
    parser.add_argument("--codebase-path", default=None, help="Path to the C/C++ codebase")
    parser.add_argument("-d", "--out-dir", default=None, help="Output directory for generated files")

    # Config file
    parser.add_argument(
        "--config-file",
        default=None,
        help="Path to global_config.yaml (default: auto-detected)",
    )

    # Analysis mode
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM enrichment in StaticAnalyzerAgent (detailed analysis for Health Report)",
    )
    parser.add_argument(
        "--llm-exclusive",
        action="store_true",
        help="Use CodebaseLLMAgent EXCLUSIVELY to generate an Excel/CSV report (skips health report, vector db, etc.)",
    )
    parser.add_argument(
        "--use-ccls",
        action="store_true",
        help="Enable CCLS dependency services for CodebaseLLMAgent",
    )

    parser.add_argument(
        "--file-to-fix",
        default=None,
        help="Specific file to analyze (relative to codebase path), used with --llm-exclusive",
    )

    # Patch analysis mode
    parser.add_argument(
        "--patch-file",
        default=None,
        help="Path to a .patch/.diff file for patch analysis (unified diff format)",
    )
    parser.add_argument(
        "--patch-target",
        default=None,
        help="Path to the original source file being patched (used with --patch-file)",
    )

    parser.add_argument(
        "--use-incremental",
        action="store_true",
        default=True,
        help="Use batch-based incremental analysis (default: enabled, handled by StaticAnalyzerAgent)",
    )

    # Deep static analysis adapters
    parser.add_argument(
        "--enable-adapters",
        action="store_true",
        default=False,
        help="Run deep static analysis adapters (Lizard, Flawfinder, CCLS) and "
             "generate detailed_code_review.xlsx with static_ tabs. "
             "Works with both standard and --llm-exclusive modes.",
    )

    # Incremental config
    parser.add_argument("--max-files", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--exclude-dirs", nargs="*", default=[])
    parser.add_argument("--exclude-globs", nargs="*", default=[])

    # Memory options
    parser.add_argument("--memory-limit", type=int, default=3000)
    parser.add_argument(
        "--enable-memory-monitoring",
        action="store_true",
        default=True,
    )

    # LLM configuration (unified format)
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        default=True,
        help="Enable LLM usage inside Agents",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="LLM model in provider::model format (e.g., 'anthropic::claude-sonnet-4-20250514'). "
             "Overrides global_config.yaml setting.",
    )
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-max-tokens", type=int, default=15000)
    parser.add_argument("--llm-temperature", type=float, default=0.1)

    # Vector DB / embedding pipeline
    parser.add_argument(
        "--enable-vector-db",
        action="store_true",
        help="Enable flattened + NDJSON generation and vector DB ingestion",
    )
    parser.add_argument("--vector-chunk-size", type=int, default=4000)
    parser.add_argument("--vector-overlap-size", type=int, default=200)
    parser.add_argument(
        "--vector-include-code",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--enable-chatbot-optimization",
        action="store_true",
        default=True,
    )

    # Visualization
    parser.add_argument(
        "--generate-visualizations",
        action="store_true",
        default=False,
    )
    parser.add_argument("--generate-pdfs", action="store_true", default=False)
    parser.add_argument("--max-edges", type=int, default=500)

    # HTML health report generation
    parser.add_argument(
        "--generate-report",
        action="store_true",
        default=False,
        help="Generate HTML healthreport.html from healthreport.json in the output directory",
    )

    # ── HITL (Human-in-the-Loop) ────────────────────────────────────────
    hitl_group = parser.add_argument_group("HITL (Human-in-the-Loop)")
    hitl_group.add_argument(
        "--enable-hitl",
        action="store_true",
        default=False,
        help="Enable Human-in-the-Loop RAG feedback system",
    )
    hitl_group.add_argument(
        "--hitl-feedback-excel",
        default=None,
        help="Path to detailed_code_review.xlsx with human feedback",
    )
    hitl_group.add_argument(
        "--hitl-constraints-dir",
        default=None,
        help="Directory to search for *_constraints.md files",
    )
    hitl_group.add_argument(
        "--hitl-store-path",
        default="./out/hitl/feedback.db",
        help="Path to HITL feedback store (SQLite database)",
    )

    # Output file paths
    parser.add_argument(
        "--health-report-path",
        default=None,
        help="Override path for healthreport.json",
    )
    parser.add_argument(
        "--flat-json-path",
        default=None,
        help="Optional override for flattened JSON output",
    )
    parser.add_argument(
        "--ndjson-path",
        default=None,
        help="Optional override for NDJSON output (for vector pipeline)",
    )

    # Processing
    parser.add_argument("--force-reanalysis", action="store_true")

    # Logging
    parser.add_argument("-v", "--verbose", action="store_true", default=None)
    parser.add_argument("-D", "--debug", action="store_true", default=None)
    parser.add_argument("--quiet", action="store_true")

    return parser.parse_args()


# --------- Helpers ---------
def validate_cpp_codebase(codebase_path: str) -> bool:
    path = Path(codebase_path)
    if not path.exists():
        console.print(f"[red]Error: Path does not exist: {codebase_path}[/red]")
        return False
    if not path.is_dir():
        console.print(f"[red]Error: Path is not a directory: {codebase_path}[/red]")
        return False

    cpp_exts = {".c", ".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".h++"}
    found = any(path.rglob(f"*{ext}") for ext in cpp_exts)
    if not found:
        console.print(f"[yellow]Warning: No C/C++ files found in {codebase_path}[/yellow]")
        console.print("Supported extensions: .c, .cpp, .cc, .cxx, .c++, .h, .hpp, .hh, .hxx, .h++")
        return False

    console.print(f"[green]✅ Valid C/C++ codebase found at {codebase_path}[/green]")
    return True


# --------- Agent Functions ---------
def codebase_analysis_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze the C/C++ codebase and write a canonical healthreport.json.

    Uses StaticAnalyzerAgent (unified 7-phase pipeline):
      1. File discovery & caching
      2. Batch analysis (all 9 analyzers)
      3. Dependency graph construction
      4. Health metrics calculation
      5. LLM enrichment (optional, via --use-llm)
      6. Report generation (JSON + Excel + Email)
      7. Visualization (Mermaid diagrams)

    Stores the full report and key sections in state.
    """
    log_memory_usage("codebase_analysis_agent start")

    opts = state.get("opts", {})
    env_config = state.get("env_config")
    global_config = state.get("global_config")

    codebase_path = (
        opts.get("codebase_path")
        or opts.get("inputpath")
        or (global_config.get("paths.code_base_path") if global_config else None)
        or env_config.get("CODE_BASE_PATH")
        or "./codebase"
    )
    out_dir = opts.get("out_dir") or "./out"

    # Canonical health report path
    default_report_dir = os.path.join(out_dir, "parseddata")
    os.makedirs(default_report_dir, exist_ok=True)

    report_path = (
        opts.get("health_report_path")
        or os.path.join(default_report_dir, "healthreport.json")
    )

    if not validate_cpp_codebase(codebase_path):
        state["codebase_analysis_status"] = "error: Invalid C/C++ codebase"
        return state

    # If report exists and not forcing reanalysis, load and return
    if os.path.exists(report_path) and not opts.get("force_reanalysis", False):
        console.print(
            f"[yellow]Health report {report_path} already exists. Skipping analysis.[/yellow]"
        )
        console.print("[yellow]Use --force-reanalysis to regenerate.[/yellow]")
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            state.update({
                "codebase_report": existing,
                "health_report_path": report_path,
                "summary": existing.get("summary", {}),
                "dependency_graph": existing.get("dependency_graph", {}),
                "health_metrics": existing.get("health_metrics", {}),
                "file_cache": existing.get("file_cache", []),
                "documentation_analysis": existing.get("documentation", {}),
                "modularization_plan": existing.get("modularization_plan", {}),
                "validation_results": existing.get("validation_report", {}),
                "codebase_analysis_status": "skipped: existing report found",
            })
        except Exception as e:
            console.print(f"[red]Error loading existing health report: {e}[/red]")
            state["codebase_analysis_status"] = f"error: {e}"
        return state

    try:
        # Unified StaticAnalyzerAgent
        if not STATIC_ANALYZER_AVAILABLE:
            raise RuntimeError("StaticAnalyzerAgent not available — check agents/codebase_static_agent.py")

        use_llm = opts.get("use_llm", False)
        console.print(f"[blue]🔧 Using StaticAnalyzerAgent (LLM={'enabled' if use_llm else 'disabled'})[/blue]")

        # Initialize LLMTools if LLM enrichment requested
        llm_tools = None
        if use_llm and LLM_TOOLS_AVAILABLE and global_config:
            try:
                llm_tools = LLMTools(config=global_config)
                console.print(f"[blue]  LLM provider: {llm_tools.provider}::{llm_tools.model}[/blue]")
            except Exception as llm_err:
                console.print(f"[yellow]Warning: LLMTools init failed: {llm_err} — running without LLM[/yellow]")
                use_llm = False

        # ── HITL context initialization ─────────────────────────────────
        hitl_context = None
        if HITL_AVAILABLE and opts.get("enable_hitl"):
            try:
                hitl_config = HITLConfig(
                    store_db_path=Path(opts.get("hitl_store_path", "./out/hitl/feedback.db")),
                )
                hitl_context = HITLContext(
                    config=hitl_config,
                    llm_tools=llm_tools if 'llm_tools' in dir() else None,
                    feedback_excel_path=opts.get("hitl_feedback_excel"),
                    constraints_dir=opts.get("hitl_constraints_dir"),
                )
                console.print("[green]✓ HITL context initialised[/green]")
                stats = hitl_context.get_statistics()
                console.print(
                    f"  Feedback store: {stats['total_decisions']} decisions, "
                    f"{stats['total_constraints']} constraints"
                )
            except Exception as exc:
                console.print(f"[yellow]⚠ HITL initialisation failed: {exc}[/yellow]")
                hitl_context = None

        agent = StaticAnalyzerAgent(
            codebase_path=codebase_path,
            output_dir=default_report_dir,
            config=global_config,
            llm_tools=llm_tools,
            max_files=opts.get("max_files", 10000),
            exclude_dirs=opts.get("exclude_dirs", []),
            exclude_globs=opts.get("exclude_globs", []),
            batch_size=opts.get("batch_size", 25),
            memory_limit_mb=opts.get("memory_limit", 3000),
            enable_llm=use_llm,
            enable_adapters=opts.get("enable_adapters", False),
            verbose=opts.get("verbose", False),
            hitl_context=hitl_context,
        )
        results = agent.run_analysis()

        if results.get("status") == "cancelled":
            state["codebase_analysis_status"] = "cancelled: user interrupted"
            return state

        # Persist canonical healthreport.json
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        console.print(f"[green]✅ Health report saved: {report_path}[/green]")

        # Update state
        health_metrics = results.get("health_metrics", {})
        state.update({
            "codebase_analysis_status": "success",
            "codebase_path": codebase_path,
            "codebase_report": results,
            "health_report_path": report_path,
            "summary": results.get("summary", {}),
            "dependency_graph": results.get("dependency_graph", {}),
            "health_metrics": health_metrics,
            "file_cache": results.get("file_cache", []),
            "documentation_analysis": results.get("documentation", {}),
            "modularization_plan": results.get("modularization_plan", {}),
            "validation_results": results.get("validation_report", {}),
        })

        # Console summary
        overall = health_metrics.get("overall_health", {})
        score = overall.get("score", 0) if overall else 0
        grade = overall.get("grade", "F") if overall else "F"

        console.print("\n[green]✅ C/C++ Analysis Complete![/green]")
        console.print(f"[blue]📊 Overall Health: {score:.1f}/100 ({grade})[/blue]")

        analyzer_scores = [
            ("dependency_score", "🔗 Dependencies"),
            ("quality_score", "🏆 Code Quality"),
            ("complexity_score", "🧩 Complexity"),
            ("maintainability_score", "🔧 Maintainability"),
            ("documentation_score", "📚 Documentation"),
            ("test_coverage_score", "🧪 Test Coverage"),
            ("security_score", "🔒 Security"),
            ("runtime_risk_score", "⚡ Runtime Risk"),
        ]
        console.print("\n[bold]📋 Detailed Scores:[/bold]")
        for key, label in analyzer_scores:
            sd = health_metrics.get(key, {})
            if isinstance(sd, dict) and "score" in sd:
                s = sd.get("score", 0)
                g = sd.get("grade", "F")
                console.print(f"  {label}: {s:.1f}/100 ({g})")
            else:
                console.print(f"  {label}: Not available")

        # Stats
        stats = health_metrics.get("statistics", {})
        if stats:
            console.print("\n[bold]📈 Statistics:[/bold]")
            console.print(f"  📁 Files Processed: {stats.get('processed_files', stats.get('total_files', 0))}")
            console.print(f"  📊 Total Lines: {stats.get('total_lines', 0):,}")
            console.print(f"  🔧 Functions Found: {stats.get('total_functions', 0):,}")
            console.print(f"  🏗️  Classes Found: {stats.get('total_classes', 0):,}")

        # Security
        sec = health_metrics.get("security_score", {})
        if isinstance(sec, dict):
            console.print("\n[bold]🔒 Security Summary:[/bold]")
            console.print(f"  Security Score: {sec.get('score', 0):.1f}/100")
            console.print(f"  Critical Issues: {sec.get('critical_issues', 0)}")
            console.print(f"  High Issues: {sec.get('high_issues', 0)}")
            console.print(f"  Total Issues: {sec.get('total_issues', 0)}")

        # Runtime Risk (New)
        rr = health_metrics.get("runtime_risk_score", {})
        if isinstance(rr, dict) and "score" in rr:
            console.print("\n[bold]⚡ Runtime Risk Summary:[/bold]")
            console.print(f"  Risk Score: {rr.get('score', 0):.1f}/100")
            rr_metrics = rr.get("metrics", {})
            console.print(f"  💀 Deadlocks: {rr_metrics.get('deadlock_issues', 0)}")
            console.print(f"  🧠 Memory Corruptions: {rr_metrics.get('memory_corruption_issues', 0)}")
            console.print(f"  🚫 Null Pointers: {rr_metrics.get('null_pointer_issues', 0)}")

        console.print(f"\n[blue]📄 Health Report: {report_path}[/blue]")
        console.print(f"[blue]💾 Final Memory: {get_memory_usage():.1f}MB[/blue]")
        force_garbage_collection()

    except Exception as e:
        console.print(f"[red]❌ Codebase analysis failed: {e}[/red]")
        logger.error("Codebase analysis failed", exc_info=True)
        log_memory_usage("analysis failed")
        state["codebase_analysis_status"] = f"error: {e}"

    return state


def flatten_and_ndjson_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes healthreport.json and generates:
    - Flattened JSON (healthreport_flat.json)
    - Embedding-ready NDJSON (*.ndjson) for VectorDbPipeline.
    """
    log_memory_usage("flatten_and_ndjson_agent start")

    opts = state.get("opts", {})
    if not opts.get("enable_vector_db", False):
        console.print("[yellow]Flattening/NDJSON generation skipped (vector DB disabled)[/yellow]")
        state["flatten_ndjson_status"] = "skipped: vector db disabled"
        return state

    if not FLATTENING_AVAILABLE:
        console.print("[yellow]Flattening tools not available (JsonFlattener/NDJSONProcessor import failed)[/yellow]")
        state["flatten_ndjson_status"] = "skipped: tools unavailable"
        return state

    health_report_path = state.get("health_report_path")
    if not health_report_path or not Path(health_report_path).exists():
        console.print("[yellow]No healthreport.json found for flattening[/yellow]")
        state["flatten_ndjson_status"] = "skipped: no healthreport.json"
        return state

    try:
        report_dir = Path(health_report_path).parent

        flat_json_path = (
            opts.get("flat_json_path")
            or str(report_dir / "healthreport_flat.json")
        )
        ndjson_path = (
            opts.get("ndjson_path")
            or str(report_dir / "healthreport_flat.ndjson")
        )

        # 1) Flatten healthreport.json -> flat JSON
        console.print("[blue]📄 Flattening healthreport.json -> flat JSON[/blue]")
        flattener = JsonFlattener()

        # JsonFlattener accepts path + optional output_path.
        flattened_records = flattener.flatten_analysis_report(
            health_report_path,
            flat_json_path,
        )

        if not isinstance(flattened_records, list):
            console.print("[yellow]Flattened output is not a list; wrapping in a list for info only[/yellow]")
            flattened_records = [flattened_records]

        console.print(
            f"[green]✅ Flattened {len(flattened_records)} records -> {flat_json_path}[/green]"
        )

        # 2) Convert flattened JSON array -> NDJSON using NDJSONWriter
        console.print("[blue]📄 Converting flattened JSON -> NDJSON (embedding-ready)[/blue]")

        ndjson_writer = NDJSONWriter(
            input_json_path=flat_json_path,
            output_ndjson_path=ndjson_path,
        )
        write_summary = ndjson_writer.run()

        console.print(
            f"[green]✅ NDJSON written: {ndjson_path} "
            f"(total={write_summary['total_entries']}, "
            f"written={write_summary['written_entries']}, "
            f"skipped={write_summary['skipped_entries']})[/green]"
        )

        # 3) Prepare embedding-ready records using NDJSONProcessor
        console.print("[blue]📄 Preparing embedding-ready records from NDJSON[/blue]")

        env_config = state.get("env_config")
        ndp = NDJSONProcessor(
            ndjson_path=ndjson_path,
            env_config=env_config,
        )
        embedding_records = ndp.generate_records()

        console.print(
            f"[green]✅ Prepared {len(embedding_records)} documents for embedding[/green]"
        )

        state.update(
            {
                "flatten_ndjson_status": "success",
                "flat_json_path": flat_json_path,
                "ndjson_path": ndjson_path,
                "flattened_record_count": len(flattened_records),
                "ndjson_record_count": write_summary.get("written_entries", 0),
                "embedding_record_count": len(embedding_records),
            }
        )
        log_memory_usage("flatten_and_ndjson_agent complete")

    except Exception as e:
        console.print(f"[red]❌ Flattening/NDJSON generation failed: {e}[/red]")
        logger.error("Flattening/NDJSON generation failed", exc_info=True)
        state["flatten_ndjson_status"] = f"error: {e}"

    return state


def postgres_db_setup_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Set up PostgreSQL database for vector storage (if enabled)."""
    log_memory_usage("postgres_db_setup_agent start")

    opts = state.get("opts", {})
    if not opts.get("enable_vector_db", False):
        state["postgres_setup_status"] = "skipped: vector db disabled"
        return state

    env_config = state.get("env_config", EnvConfig())
    try:
        console.print("[blue]🗄️  Setting up PostgreSQL database...[/blue]")
        setup = PostgresDbSetup(environment=env_config)
        setup.run()
        state.update({
            "postgres_setup_status": "success",
            "postgres_db_info": {
                "host": setup.host,
                "port": setup.port,
                "database": setup.database,
                "username": setup.username,
                "password": setup.password,
                "collection_table": setup.collection_table,
                "embedding_table": setup.embedding_table,
            },
        })
        console.print("[green]✅ PostgreSQL setup completed successfully[/green]")
        log_memory_usage("postgres_db_setup_agent complete")
    except Exception as e:
        console.print(f"[red]❌ PostgreSQL setup failed: {e}[/red]")
        logger.error("PostgreSQL setup failed", exc_info=True)
        state["postgres_setup_status"] = f"error: {e}"

    return state


def vector_db_ingestion_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ingest NDJSON documents into PostgreSQL vector DB via VectorDbPipeline.
    """
    log_memory_usage("vector_db_ingestion_agent start")

    opts = state.get("opts", {})
    if not opts.get("enable_vector_db", False):
        console.print("[yellow]Vector DB ingestion skipped (not enabled)[/yellow]")
        state["vector_db_ingestion_status"] = "skipped: not enabled"
        return state

    if not VECTOR_DB_PIPELINE_AVAILABLE:
        console.print("[yellow]VectorDbPipeline not available[/yellow]")
        state["vector_db_ingestion_status"] = "skipped: pipeline unavailable"
        return state

    ndjson_path = state.get("ndjson_path")
    if not ndjson_path or not Path(ndjson_path).exists():
        console.print("[yellow]No NDJSON file found for ingestion[/yellow]")
        state["vector_db_ingestion_status"] = "skipped: no ndjson"
        return state

    try:
        env_config = state.get("env_config")
        console.print("[blue]🔗 Ingesting NDJSON into PostgreSQL vector DB...[/blue]")

        os.environ["NDJSON_PATH"] = str(Path(ndjson_path).parent)
        pipeline = VectorDbPipeline(environment=env_config)
        pipeline.chunk_size = opts.get("vector_chunk_size", 500)

        pipeline.run()

        state.update({
            "vector_db_ingestion_status": "success",
            "ingested_document_count": state.get("ndjson_record_count", 0),
            "ndjson_path": ndjson_path,
        })

        console.print(
            f"[green]✅ Vector DB ingestion complete (NDJSON: {ndjson_path})[/green]"
        )
        log_memory_usage("vector_db_ingestion_agent complete")

    except Exception as e:
        console.print(f"[red]❌ Vector DB ingestion failed: {e}[/red]")
        logger.error("Vector DB ingestion failed", exc_info=True)
        state["vector_db_ingestion_status"] = f"error: {e}"

    return state


# --------- LangGraph Workflow ---------
def build_workflow_graph():
    """
    Build the LangGraph workflow for C/C++ codebase analysis + vector DB pipeline.
    """
    graph = StateGraph(dict)

    graph.add_node("postgres_db_setup", postgres_db_setup_agent)
    graph.add_node("codebase_analysis", codebase_analysis_agent)
    graph.add_node("flatten_and_ndjson", flatten_and_ndjson_agent)
    graph.add_node("vector_db_ingestion", vector_db_ingestion_agent)

    # Sequential pipeline
    graph.add_edge("postgres_db_setup", "codebase_analysis")
    graph.add_edge("codebase_analysis", "flatten_and_ndjson")
    graph.add_edge("flatten_and_ndjson", "vector_db_ingestion")

    graph.set_entry_point("postgres_db_setup")
    return graph


def run_workflow(user_input: str, env_config: EnvConfig, global_config: Optional[Any], opts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the full workflow, from analysis to NDJSON and vector DB ingestion.
    """
    log_memory_usage("workflow start")

    codebase_path = (
        opts.get("codebase_path")
        or opts.get("inputpath")
        or (global_config.get("paths.code_base_path") if global_config else None)
        or env_config.get("CODE_BASE_PATH")
        or "./codebase"
    )
    out_dir = (
        opts.get("out_dir")
        or opts.get("outputpath")
        or (global_config.get("paths.out_dir") if global_config else None)
        or "./out"
    )

    state: Dict[str, Any] = {
        "user_input": user_input,
        "env_config": env_config,
        "global_config": global_config,
        "opts": opts,
        "codebase_path": codebase_path,
        "out_dir": out_dir,
        "REPORT_JSON_PATH": os.path.join(out_dir, "parseddata"),
        "MAX_EDGES": opts.get("max_edges", 500),
    }

    adapters_active = opts.get("enable_adapters", False) and ADAPTERS_AVAILABLE

    analysis_mode = "StaticAnalyzerAgent"
    mode_parts = []
    if opts.get("use_llm", False):
        mode_parts.append("LLM enrichment")
    if adapters_active:
        mode_parts.append("deep static adapters")
    if mode_parts:
        analysis_mode += " + " + " + ".join(mode_parts)
    else:
        analysis_mode += " (static analysis only)"

    console.print("\n[bold blue]🚀 Starting C/C++ Codebase Analysis Workflow[/bold blue]")
    console.print(f"[blue]📁 Codebase: {codebase_path}[/blue]")
    console.print(f"[blue]📂 Output: {out_dir}[/blue]")
    console.print(f"[blue]🔄 Analysis Mode: {analysis_mode}[/blue]")
    console.print(
        f"[blue]🗄️  PostgreSQL Vector DB: "
        f"{'enabled' if opts.get('enable_vector_db', False) else 'disabled'}[/blue]"
    )
    console.print(
        f"[blue]🤖 StaticAnalyzerAgent: "
        f"{'available' if STATIC_ANALYZER_AVAILABLE else 'unavailable'}[/blue]"
    )
    if adapters_active:
        console.print(
            "[blue]🔬 Deep Adapters: enabled "
            "(Lizard, Flawfinder, CCLS)[/blue]"
        )

    graph = build_workflow_graph()
    workflow = graph.compile()

    start = time.time()
    final_state = workflow.invoke(state)
    elapsed = time.time() - start

    log_memory_usage("workflow complete")

    console.print("\n[bold green]🏁 Workflow Complete![/bold green]")
    console.print(f"[green]⏱️  Total Time: {elapsed:.1f} seconds[/green]")
    console.print(f"[green]💾 Final Memory: {get_memory_usage():.1f}MB[/green]")

    console.print("\n[bold]📊 Agent Status Summary:[/bold]")
    for key, label in [
        ("postgres_setup_status", "🗄️  PostgreSQL Setup"),
        ("codebase_analysis_status", "🔍 Codebase Analysis"),
        ("flatten_ndjson_status", "📄 Flatten + NDJSON"),
        ("vector_db_ingestion_status", "🔗 Vector DB Ingestion"),
    ]:
        status = final_state.get(key, "unknown")
        if status == "success":
            console.print(f"  [green]✅ {label}: Success[/green]")
        elif isinstance(status, str) and status.startswith("error:"):
            console.print(f"  [red]❌ {label}: {status}[/red]")
        elif isinstance(status, str) and status.startswith("skipped:"):
            console.print(f"  [yellow]⏭️  {label}: {status}[/yellow]")
        else:
            console.print(f"  [blue]ℹ️  {label}: {status}[/blue]")

    # Key outputs
    if final_state.get("health_report_path"):
        health_metrics = final_state.get("health_metrics", {})
        overall = health_metrics.get("overall_health", {})
        score = overall.get("score", 0) if overall else 0
        grade = overall.get("grade", "F") if overall else "F"

        console.print("\n[bold]📋 Key Outputs:[/bold]")
        console.print(f"  📊 Health Report: {final_state['health_report_path']}")
        console.print(f"  🏥 Overall Health: {score:.1f}/100 ({grade})")

        stats = health_metrics.get("statistics", {})
        if stats:
            console.print(
                f"  📈 Files Processed: "
                f"{stats.get('processed_files', stats.get('total_files', 0))}"
            )
            console.print(f"  📊 Total Lines: {stats.get('total_lines', 0):,}")
            console.print(f"  🔧 Functions Found: {stats.get('total_functions', 0):,}")
            console.print(f"  🏗️  Classes Found: {stats.get('total_classes', 0):,}")

    if final_state.get("ndjson_path"):
        console.print("\n[bold]🔗 Vector DB Artifacts:[/bold]")
        console.print(f"  📄 NDJSON: {final_state['ndjson_path']}")
        if final_state.get("ndjson_record_count") is not None:
            console.print(
                f"  📊 NDJSON Records: {final_state['ndjson_record_count']}"
            )
        if final_state.get("ingested_document_count") is not None:
            console.print(
                f"  🗄️  Documents Ingested: {final_state['ingested_document_count']}"
            )

    # ── CCLS artifact cleanup ────────────────────────────────────────────
    _opts = state.get("opts", {})
    if _opts.get("use_ccls", False):
        try:
            from dependency_builder.cleanup import cleanup_ccls_artifacts
            out_dir = state.get("out_dir", "./out")
            src_dir = state.get("codebase_path", "")
            console.print("\n[blue]🧹 Cleaning up CCLS artifacts...[/blue]")
            cleanup_stats = cleanup_ccls_artifacts(
                output_dir=out_dir,
                project_root=src_dir,
            )
            mb = cleanup_stats["bytes_freed"] / (1024 * 1024)
            console.print(
                f"[green]  ✅ Removed {cleanup_stats['files_removed']} files, "
                f"{cleanup_stats['dirs_removed']} dirs ({mb:.1f} MB freed)[/green]"
            )
        except Exception as e:
            console.print(f"[yellow]  ⚠️  CCLS cleanup failed: {e}[/yellow]")

    force_garbage_collection()
    return final_state


# --------- Standalone Adapter Runner ---------
def _run_standalone_adapters(
    codebase_path: str,
    output_dir: str,
    exclude_dirs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    generate_excel: bool = False,
) -> Dict[str, Any]:
    """
    Run deep static analysis adapters independently.

    Builds a minimal file_cache by scanning the codebase and runs all adapters.
    When called from --llm-exclusive mode, returns results for CodebaseLLMAgent
    to embed static_ tabs into its own Excel. When generate_excel=True, writes
    a standalone detailed_code_review.xlsx via ExcelReportAdapter.

    :param codebase_path: Root path of the C/C++ codebase.
    :param output_dir: Directory for output artifacts.
    :param exclude_dirs: Directories to exclude from scanning.
    :param exclude_globs: Glob patterns to exclude from scanning.
    :param generate_excel: If True, generate a standalone Excel report.
    :return: Dict mapping adapter names to their result dicts.
    """
    from agents.core.file_processor import FileProcessor

    # Build file cache from codebase
    processor = FileProcessor(
        codebase_path=codebase_path,
        exclude_dirs=exclude_dirs or [],
        exclude_globs=exclude_globs or [],
    )
    file_cache = processor.process_files()
    console.print(f"[blue]  📁 File cache: {len(file_cache)} files[/blue]")

    # Try to initialize CCLS
    ccls_navigator = None
    try:
        from dependency_builder.ccls_code_navigator import CCLSCodeNavigator
        from dependency_builder.config import DependencyBuilderConfig

        config = DependencyBuilderConfig.from_env()
        cache_path = os.path.join(output_dir, ".ccls_cache")
        os.makedirs(cache_path, exist_ok=True)
        ccls_navigator = CCLSCodeNavigator(
            project_root=codebase_path,
            cache_path=cache_path,
            logger=logger,
            config=config,
        )
    except Exception as exc:
        console.print(f"[yellow]  CCLS not available: {exc}[/yellow]")

    # Run all adapters
    adapter_results: Dict[str, Any] = {}
    adapters = [
        ("ast_complexity", ASTComplexityAdapter()),
        ("security", SecurityAdapter()),
        ("dead_code", DeadCodeAdapter()),
        ("call_graph", CallGraphAdapter()),
        ("function_metrics", FunctionMetricsAdapter()),
    ]

    for name, adapter in adapters:
        try:
            result = adapter.analyze(
                file_cache, ccls_navigator=ccls_navigator, dependency_graph={}
            )
            adapter_results[name] = result
            avail = result.get("tool_available", False)
            score = result.get("score", 0)
            grade = result.get("grade", "F")
            if avail:
                console.print(f"[green]  ✅ {name}: {score:.0f}/100 ({grade})[/green]")
            else:
                console.print(f"[yellow]  ⏭️  {name}: tool not available[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]  ⏭️  {name}: failed ({exc})[/yellow]")

    # Optionally generate a standalone Excel report with static_ tabs
    if generate_excel:
        excel_adapter = ExcelReportAdapter(output_dir=output_dir)
        excel_adapter.analyze(file_cache=[], adapter_results=adapter_results)
        report_path = os.path.join(output_dir, "detailed_code_review.xlsx")
        console.print(f"[green]  📊 Static analysis tabs written to: {report_path}[/green]")

    # Cleanup CCLS
    if ccls_navigator is not None:
        try:
            ccls_navigator.killCCLSProcess()
        except Exception:
            pass

    return adapter_results


# --------- Main Entrypoint ---------
def main():
    try:
        opts = vars(parse_args())

        # Load GlobalConfig if available, otherwise fallback to EnvConfig
        global_config = None
        if GLOBAL_CONFIG_AVAILABLE:
            try:
                config_file = opts.get("config_file")
                if config_file:
                    global_config = GlobalConfig(config_file)
                else:
                    global_config = GlobalConfig()
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load global_config: {e}[/yellow]")
                global_config = None

        env_config = EnvConfig()

        # Logging level
        if opts.get("debug"):
            logging.getLogger().setLevel(logging.DEBUG)
        elif opts.get("verbose"):
            logging.getLogger().setLevel(logging.INFO)
        elif opts.get("quiet"):
            logging.getLogger().setLevel(logging.WARNING)

        log_memory_usage("startup")

        # Resolve codebase path (with global config support)
        codebase_path = (
            opts.get("codebase_path")
            or opts.get("inputpath")
            or (global_config.get("paths.code_base_path") if global_config else None)
            or env_config.get("CODE_BASE_PATH")
            or "./codebase"
        )

        # ── Single-file convenience: if --file-to-fix is an absolute path and
        # no explicit --codebase-path was given, derive codebase from the file's
        # parent directory so the user doesn't have to specify both.
        file_to_fix = opts.get("file_to_fix")
        if file_to_fix:
            ftf_path = Path(file_to_fix)
            # If the file is absolute and codebase_path is just the default
            if ftf_path.is_absolute() and codebase_path == "./codebase":
                codebase_path = str(ftf_path.parent)
                console.print(
                    f"[blue]ℹ --file-to-fix is absolute; using parent as "
                    f"codebase path: {codebase_path}[/blue]"
                )
            # If relative file_to_fix doesn't exist under codebase_path, check
            # if it exists as an absolute path and adjust accordingly
            elif not ftf_path.is_absolute():
                resolved = Path(codebase_path) / ftf_path
                if not resolved.exists() and ftf_path.exists():
                    # The path is actually relative to CWD, not codebase
                    abs_ftf = ftf_path.resolve()
                    opts["file_to_fix"] = str(abs_ftf)
                    if codebase_path == "./codebase":
                        codebase_path = str(abs_ftf.parent)
                        console.print(
                            f"[blue]ℹ Resolved --file-to-fix to: {abs_ftf}; "
                            f"using parent as codebase path: {codebase_path}[/blue]"
                        )

        if not Path(codebase_path).exists():
            console.print(f"[red]Error: Codebase path does not exist: {codebase_path}[/red]")
            console.print("[yellow]Use --codebase-path to specify the correct path[/yellow]")
            sys.exit(1)
        opts["codebase_path"] = codebase_path

        # Output dir (with global config support)
        if not opts.get("out_dir"):
            opts["out_dir"] = (
                opts.get("outputpath")
                or (global_config.get("paths.out_dir") if global_config else None)
                or "./out"
            )

        # Ensure output directory exists
        if not os.path.exists(opts["out_dir"]):
            os.makedirs(opts["out_dir"], exist_ok=True)

        # ── Merge exclude_dirs / exclude_globs from config + CLI ──────────
        # CLI args are merged ON TOP of global_config.yaml entries so that
        # permanent exclusions live in the config and ad-hoc ones on the CLI.
        if global_config:
            cfg_exclude_dirs = global_config.get_list("scanning.exclude_dirs", default=[])
            cfg_exclude_globs = global_config.get_list("scanning.exclude_globs", default=[])
        else:
            cfg_exclude_dirs = []
            cfg_exclude_globs = []
        cli_exclude_dirs = opts.get("exclude_dirs") or []
        cli_exclude_globs = opts.get("exclude_globs") or []
        # Deduplicated merge (config first, then CLI additions)
        opts["exclude_dirs"] = list(dict.fromkeys(cfg_exclude_dirs + cli_exclude_dirs))
        opts["exclude_globs"] = list(dict.fromkeys(cfg_exclude_globs + cli_exclude_globs))
        if opts["exclude_dirs"]:
            console.print(f"[dim]Exclude dirs: {opts['exclude_dirs']}[/dim]")
        if opts["exclude_globs"]:
            console.print(f"[dim]Exclude globs: {opts['exclude_globs']}[/dim]")

        # ── Setup debug.log file handler ─────────────────────────────────
        # Always write DEBUG-level messages to {out_dir}/debug.log so the user
        # can inspect CCLS context, prompt dumps, and other diagnostics even
        # when the console is at INFO or WARNING level.
        _debug_log_path = os.path.join(opts["out_dir"], "debug.log")
        _debug_fh = logging.FileHandler(_debug_log_path, mode="w", encoding="utf-8")
        _debug_fh.setLevel(logging.DEBUG)
        _debug_fh.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ))
        logging.getLogger().addHandler(_debug_fh)
        # Ensure root logger accepts DEBUG so the file handler receives everything
        if logging.getLogger().level > logging.DEBUG:
            # Keep console handler at its current level; only root needs DEBUG
            for _h in logging.getLogger().handlers:
                if isinstance(_h, logging.StreamHandler) and not isinstance(_h, logging.FileHandler):
                    _h.setLevel(logging.getLogger().level)
            logging.getLogger().setLevel(logging.DEBUG)
        console.print(f"[dim]Debug log: {_debug_log_path}[/dim]")

        # ----------------------------------------------------
        # PATCH ANALYSIS MODE
        # ----------------------------------------------------
        if opts.get("patch_file") and opts.get("patch_target"):
            if not PATCH_AGENT_AVAILABLE:
                console.print("[red]❌ CodebasePatchAgent not available. Check agents/codebase_patch_agent.py[/red]")
                sys.exit(1)

            console.print(f"[bold blue]🔍 Starting Patch Analysis[/bold blue]")
            console.print(f"[blue]📄 Target file: {opts['patch_target']}[/blue]")
            console.print(f"[blue]📋 Patch file:  {opts['patch_file']}[/blue]")

            try:
                # Build LLM tools
                llm_model = opts.get("llm_model") or (global_config.get("llm.model") if global_config else None)
                llm_tools = None
                if LLM_TOOLS_AVAILABLE:
                    try:
                        llm_tools = LLMTools(model=llm_model) if llm_model else LLMTools()
                    except Exception as llm_err:
                        console.print(f"[yellow]Warning: LLMTools init failed: {llm_err}[/yellow]")

                # HITL context
                hitl_context = None
                if HITL_AVAILABLE and opts.get("enable_hitl"):
                    try:
                        hitl_config = HITLConfig(
                            store_db_path=Path(opts.get("hitl_store_path", "./out/hitl/feedback.db")),
                        )
                        hitl_context = HITLContext(
                            config=hitl_config,
                            llm_tools=llm_tools,
                            feedback_excel_path=opts.get("hitl_feedback_excel"),
                            constraints_dir=opts.get("hitl_constraints_dir"),
                        )
                        console.print("[green]✓ HITL context initialised[/green]")
                    except Exception as exc:
                        console.print(f"[yellow]⚠ HITL init failed: {exc}[/yellow]")

                agent = CodebasePatchAgent(
                    file_path=opts["patch_target"],
                    patch_file=opts["patch_file"],
                    output_dir=opts.get("out_dir", "./out"),
                    config=global_config,
                    llm_tools=llm_tools,
                    hitl_context=hitl_context,
                    enable_adapters=opts.get("enable_adapters", False),
                    verbose=opts.get("verbose", False),
                )

                excel_path = os.path.join(opts.get("out_dir", "./out"), "detailed_code_review.xlsx")
                result = agent.run_analysis(excel_path=excel_path)

                console.print(f"\n[green]✅ Patch Analysis Complete![/green]")
                console.print(f"[blue]📊 Original issues: {result.get('original_issue_count', 0)}[/blue]")
                console.print(f"[blue]📊 Patched issues:  {result.get('patched_issue_count', 0)}[/blue]")
                console.print(f"[bold]📊 NEW issues:      {result.get('new_issue_count', 0)}[/bold]")
                console.print(f"[green]📄 Excel output:    {result.get('excel_path', 'N/A')}[/green]")

                sys.exit(0)

            except Exception as e:
                console.print(f"[red]❌ Patch Analysis failed: {e}[/red]")
                logger.error("Patch Analysis failed", exc_info=True)
                sys.exit(1)

        # ----------------------------------------------------
        # EXCLUSIVE LLM MODE (Bypass Workflow)
        # ----------------------------------------------------
        if opts.get("llm_exclusive"):
            if not LLM_EXCLUSIVE_AGENT_AVAILABLE:
                console.print("[red]❌ CodebaseLLMAgent not available. Check agents/codebase_llm_agent.py[/red]")
                sys.exit(1)

            console.print(f"[bold blue]🚀 Starting Exclusive LLM Analysis on: {codebase_path}[/bold blue]")
            if opts.get("enable_vector_db"):
                console.print("[blue]ℹ️  (Bypassing Health Report — LLM results will be ingested into Vector DB)[/blue]")
            else:
                console.print("[blue]ℹ️  (Bypassing Health Report & Vector DB Workflow)[/blue]")

            try:
                # Build shared LLM tools from config/CLI args
                llm_model = opts.get("llm_model") or (global_config.get("llm.model") if global_config else None)

                if LLM_TOOLS_AVAILABLE and llm_model:
                    llm_tools = LLMTools(model=llm_model)
                elif LLM_TOOLS_AVAILABLE:
                    llm_tools = LLMTools()
                else:
                    llm_tools = None

                # ── HITL context initialization for LLM-exclusive mode ─────────────────
                hitl_context = None
                if HITL_AVAILABLE and opts.get("enable_hitl"):
                    try:
                        hitl_config = HITLConfig(
                            store_db_path=Path(opts.get("hitl_store_path", "./out/hitl/feedback.db")),
                        )
                        hitl_context = HITLContext(
                            config=hitl_config,
                            llm_tools=llm_tools,
                            feedback_excel_path=opts.get("hitl_feedback_excel"),
                            constraints_dir=opts.get("hitl_constraints_dir"),
                        )
                        console.print("[green]✓ HITL context initialised[/green]")
                        stats = hitl_context.get_statistics()
                        console.print(
                            f"  Feedback store: {stats['total_decisions']} decisions, "
                            f"{stats['total_constraints']} constraints"
                        )
                    except Exception as exc:
                        console.print(f"[yellow]⚠ HITL initialisation failed: {exc}[/yellow]")
                        hitl_context = None

                # Initialize Agent with new config/llm_tools parameters
                agent = CodebaseLLMAgent(
                    codebase_path=opts["codebase_path"],
                    output_dir=opts["out_dir"],
                    config=global_config,
                    llm_tools=llm_tools,
                    exclude_dirs=opts.get("exclude_dirs", []),
                    exclude_globs=opts.get("exclude_globs", []),
                    max_files=opts.get("max_files", 10000),
                    use_ccls=opts.get("use_ccls", False),
                    file_to_fix=opts.get("file_to_fix"),
                    hitl_context=hitl_context,
                )

                # Determine output filename
                output_filename = "detailed_code_review.xlsx"

                # Optionally run deep static adapters FIRST, so results
                # are passed into CodebaseLLMAgent for a single Excel file
                adapter_results = None
                if opts.get("enable_adapters", False) and ADAPTERS_AVAILABLE:
                    console.print("[blue]🔬 Running deep static analysis adapters...[/blue]")
                    try:
                        adapter_results = _run_standalone_adapters(
                            codebase_path=opts["codebase_path"],
                            output_dir=opts["out_dir"],
                            exclude_dirs=opts.get("exclude_dirs", []),
                            exclude_globs=opts.get("exclude_globs", []),
                        )
                        console.print("[green]✅ Adapter analysis complete — results will be merged into Excel.[/green]")
                    except Exception as adapter_err:
                        console.print(f"[yellow]Warning: Adapter analysis failed: {adapter_err}[/yellow]")
                        adapter_results = None

                # Run LLM Analysis (passes adapter_results for single-file output)
                report_path = agent.run_analysis(
                    output_filename=output_filename,
                    email_recipients=None,
                    adapter_results=adapter_results,
                )

                console.print(f"[green]✅ LLM Analysis Complete![/green]")
                console.print(f"[green]📊 Detailed Report Saved: {report_path}[/green]")

                # ── Vector DB ingestion (optional) ────────────────────────
                if opts.get("enable_vector_db") and getattr(agent, "results", None):
                    try:
                        parseddata_dir = os.path.join(opts["out_dir"], "parseddata")
                        os.makedirs(parseddata_dir, exist_ok=True)
                        ndjson_out = os.path.join(parseddata_dir, "llm_review_vector.ndjson")
                        agent._write_vector_ndjson(ndjson_out)
                        console.print(f"[green]✓ Vector-ready NDJSON: {ndjson_out}[/green]")

                        from db.vectordb_pipeline import VectorDbPipeline
                        env_cfg = global_config if global_config else EnvConfig()
                        pipeline = VectorDbPipeline(environment=env_cfg)
                        pipeline.run()
                        console.print("[green]✓ Vector DB ingestion complete — chat is now available[/green]")
                    except Exception as vec_err:
                        console.print(f"[yellow]⚠ Vector DB ingestion skipped: {vec_err}[/yellow]")

                sys.exit(0)

            except Exception as e:
                console.print(f"[red]❌ Exclusive LLM Analysis failed: {e}[/red]")
                logger.error("Exclusive LLM Analysis failed", exc_info=True)
                sys.exit(1)

        # ----------------------------------------------------
        # STANDARD WORKFLOW
        # ----------------------------------------------------
        user_input = f"Analyze codebase at {codebase_path}"
        final_state = run_workflow(user_input, env_config, global_config, opts)

        # Optional: generate HTML health report
        if opts.get("generate_report", False) and HEALTHREPORT_GENERATOR_AVAILABLE:
            health_report_path = final_state.get("health_report_path")
            if health_report_path and Path(health_report_path).exists():
                run_health_report(healthreport_path=health_report_path, output_dir=opts["out_dir"])

        sys.exit(0)

    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]❌ Unexpected error: {e}[/red]")
        logger.error("Unexpected error", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
