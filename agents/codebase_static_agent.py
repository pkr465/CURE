"""
Unified C/C++ Static Analyzer Agent.

Consolidates:
- Memory-efficient batch processing (from IncrementalCodebaseAnalyzer)
- LLM-enriched analysis pipeline (from CodebaseAnalysisAgent)
- All 9 specialized analyzers via MetricsCalculator
- Deep static analysis adapters (CCLS/libclang, Lizard, Flawfinder) via MetricsCalculator

7-Phase Pipeline:
1. File Discovery & Caching (FileProcessor)
2. Batch Analysis with Memory Management
3. Dependency Graph Building (DependencyAnalyzer)
4. Health Metrics Calculation (MetricsCalculator)
5. LLM Enrichment (PromptTemplates + LLMTools)
6. Report Generation (JSON + Excel + Email)
7. Visualization (Mermaid diagrams)

Usage:
    agent = StaticAnalyzerAgent(
        codebase_path="/path/to/code",
        output_dir="./out",
        enable_llm=True,
    )
    results = agent.run_analysis()
"""

import os
import re
import json
import logging
import gc
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple
from datetime import datetime
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)

# ============================================================================
# AVAILABILITY FLAGS: Try/except imports with feature detection
# ============================================================================

try:
    from agents.core.file_processor import FileProcessor
    FILE_PROCESSOR_AVAILABLE = True
except ImportError:
    FILE_PROCESSOR_AVAILABLE = False
    logger.warning("FileProcessor not available")

try:
    from agents.core.metrics_calculator import MetricsCalculator
    METRICS_CALCULATOR_AVAILABLE = True
except ImportError:
    METRICS_CALCULATOR_AVAILABLE = False
    logger.warning("MetricsCalculator not available")

try:
    from agents.analyzers.dependency_analyzer import DependencyAnalyzer, AnalyzerConfig
    DEPENDENCY_ANALYZER_AVAILABLE = True
except ImportError:
    DEPENDENCY_ANALYZER_AVAILABLE = False
    logger.warning("DependencyAnalyzer not available")

try:
    from agents.prompts.prompts import PromptTemplates
    PROMPT_TEMPLATES_AVAILABLE = True
except ImportError:
    PROMPT_TEMPLATES_AVAILABLE = False
    logger.warning("PromptTemplates not available")

try:
    from utils.common.llm_tools import LLMTools
    LLM_TOOLS_AVAILABLE = True
except ImportError:
    LLM_TOOLS_AVAILABLE = False
    logger.warning("LLMTools not available")

try:
    from utils.parsers.global_config_parser import GlobalConfig
    GLOBAL_CONFIG_AVAILABLE = True
except ImportError:
    GLOBAL_CONFIG_AVAILABLE = False
    logger.warning("GlobalConfig not available")

try:
    from utils.common.email_reporter import EmailReporter
    EMAIL_REPORTER_AVAILABLE = True
except ImportError:
    EMAIL_REPORTER_AVAILABLE = False
    logger.warning("EmailReporter not available")

try:
    from utils.common.excel_writer import ExcelWriter
    EXCEL_WRITER_AVAILABLE = True
except ImportError:
    EXCEL_WRITER_AVAILABLE = False
    logger.warning("ExcelWriter not available")

try:
    from agents.visualization.graph_generator import GraphGenerator
    GRAPH_GENERATOR_AVAILABLE = True
except ImportError:
    GRAPH_GENERATOR_AVAILABLE = False
    logger.warning("GraphGenerator not available")

try:
    import rich.console
    import rich.progress
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    logger.warning("Rich not available for progress display")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available for memory monitoring")

# HITL support (optional)
try:
    from hitl import HITLContext, HITL_AVAILABLE
except ImportError:
    HITLContext = None
    HITL_AVAILABLE = False


# ============================================================================
# STATIC ANALYZER AGENT
# ============================================================================

class StaticAnalyzerAgent:
    """
    Unified C/C++ Static Analyzer Agent with 7-phase pipeline.

    Consolidates memory-efficient batch processing and LLM-enriched analysis.
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cc", ".cpp", ".cxx"}
    H_EXTS = {".h", ".hh", ".hpp", ".hxx"}
    DEFAULT_EXTS = sorted(list(C_EXTS | H_EXTS))

    # LLM chunking constants
    CHUNK_MAX_ITEMS = 200

    def __init__(
        self,
        codebase_path: str,
        output_dir: str = "./out",
        config: Optional['GlobalConfig'] = None,
        llm_tools: Optional['LLMTools'] = None,
        file_extensions: Optional[List[str]] = None,
        max_files: int = 10000,
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
        batch_size: int = 25,
        memory_limit_mb: int = 3000,
        enable_llm: bool = True,
        enable_adapters: bool = False,
        verbose: bool = False,
        hitl_context: Optional['HITLContext'] = None,
    ):
        """
        Initialize the unified static analyzer agent.

        Args:
            codebase_path: Path to C/C++ codebase
            output_dir: Output directory for reports
            config: Optional GlobalConfig instance
            llm_tools: Optional LLMTools instance
            file_extensions: List of file extensions to analyze
            max_files: Maximum files to process
            exclude_dirs: Directories to exclude
            exclude_globs: Glob patterns to exclude
            batch_size: Batch size for processing files
            memory_limit_mb: Memory limit in MB before forcing GC
            enable_llm: Enable LLM enrichment
            enable_adapters: Enable deep static analysis adapters (Lizard, Flawfinder, CCLS)
            verbose: Enable verbose output with Rich progress bars
        """
        # Path resolution and validation
        self.codebase_path = Path(codebase_path).resolve()
        if not self.codebase_path.exists():
            raise ValueError(f"Codebase path does not exist: {self.codebase_path}")
        
        # Ensure codebase path is actually a directory to prevent confusion
        if not self.codebase_path.is_dir():
            raise ValueError(f"Codebase path must be a directory: {self.codebase_path}")

        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # File processing parameters
        self.file_extensions = file_extensions or self.DEFAULT_EXTS
        self.max_files = max_files
        self.batch_size = batch_size
        self.memory_limit_mb = memory_limit_mb
        self.enable_llm = enable_llm
        self.verbose = verbose

        # Exclusions
        self.exclude_dirs = set(
            exclude_dirs or [
                ".git", "build", "dist", ".idea", ".vscode", ".tox",
                "node_modules", "third_party", "__pycache__", ".mypy_cache",
                ".pytest_cache", "out", "bin", "obj"
            ]
        )
        self.exclude_globs = exclude_globs or []

        # Initialize GlobalConfig
        if config is None and GLOBAL_CONFIG_AVAILABLE:
            try:
                self.config = GlobalConfig()
            except Exception as e:
                logger.warning(f"Failed to load GlobalConfig: {e}")
                self.config = None
        else:
            self.config = config

        # Initialize LLMTools
        self.llm_tools = None
        if enable_llm:
            if llm_tools is not None:
                self.llm_tools = llm_tools
            elif LLM_TOOLS_AVAILABLE:
                try:
                    self.llm_tools = LLMTools()
                except Exception as e:
                    logger.warning(f"Failed to initialize LLMTools: {e}")

        # Initialize analyzers
        self.file_processor = None
        if FILE_PROCESSOR_AVAILABLE:
            try:
                self.file_processor = FileProcessor(
                    codebase_path=str(self.codebase_path),
                    max_files=max_files,
                    exclude_dirs=list(self.exclude_dirs),
                    exclude_globs=self.exclude_globs,
                )
            except Exception as e:
                logger.warning(f"Failed to initialize FileProcessor: {e}")

        self.metrics_calculator = None
        if METRICS_CALCULATOR_AVAILABLE:
            try:
                self.metrics_calculator = MetricsCalculator(
                    codebase_path=str(self.codebase_path),
                    output_dir=str(self.output_dir),
                    project_root=str(self.codebase_path),
                    debug=False,
                    enable_adapters=enable_adapters,
                )
            except Exception as e:
                logger.warning(f"Failed to initialize MetricsCalculator: {e}")

        self.dependency_analyzer = None
        if DEPENDENCY_ANALYZER_AVAILABLE:
            try:
                analyzer_config = AnalyzerConfig(
                    project_root=str(self.codebase_path),
                    ignore_dirs=[str(d) for d in self.exclude_dirs],
                )
                self.dependency_analyzer = DependencyAnalyzer(analyzer_config)
            except Exception as e:
                logger.warning(f"Failed to initialize DependencyAnalyzer: {e}")

        # Rich console for verbose output
        self.console = None
        if verbose and RICH_AVAILABLE:
            try:
                self.console = rich.console.Console()
            except Exception as e:
                logger.warning(f"Failed to initialize Rich console: {e}")

        # HITL context
        self.hitl_context = hitl_context

        # State containers
        self.file_cache: List[Dict[str, Any]] = []
        self.dependency_graph: Dict[str, Any] = {}
        self.health_metrics: Dict[str, Any] = {}
        self.llm_enrichment: Dict[str, Any] = {}
        self.documentation: Dict[str, Any] = {}
        self.modularization_plan: Dict[str, Any] = {}
        self.validation_report: Dict[str, Any] = {}
        self.final_report: Dict[str, Any] = {}
        self.errors: List[Dict[str, Any]] = []
        self.report_files: Dict[str, str] = {}
        self.visualizations: Dict[str, str] = {}

    # ========================================================================
    # PHASE 1: FILE DISCOVERY & CACHING
    # ========================================================================

    def discover_and_cache_files(self) -> List[Dict[str, Any]]:
        """
        Discover C/C++ files and build file cache using FileProcessor.

        Returns:
            List of file cache entries
        """
        if not FILE_PROCESSOR_AVAILABLE or self.file_processor is None:
            logger.error("FileProcessor not available")
            self.errors.append({
                "stage": "discover_and_cache_files",
                "error": "FileProcessor not available"
            })
            return []

        try:
            if self.verbose and self.console:
                self.console.print("[bold cyan]Phase 1: Discovering and caching files...[/bold cyan]")

            raw_file_cache = self.file_processor.process_files()

            # ----------------------------------------------------------------
            # VALIDATION FIX: Robust filtering of directories and invalid paths
            # This prevents "[Errno 21] Is a directory" and "File not found" 
            # errors during analysis phases.
            # ----------------------------------------------------------------
            valid_files = []
            for entry in raw_file_cache:
                f_path = None
                
                # 1. Determine path
                try:
                    if "absolute_path" in entry and entry["absolute_path"]:
                        f_path = Path(entry["absolute_path"]).resolve()
                    elif "file_relative_path" in entry and entry["file_relative_path"]:
                        f_path = (self.codebase_path / entry["file_relative_path"]).resolve()
                except Exception as e:
                    logger.warning(f"Error resolving path for entry {entry}: {e}")
                    continue

                # 2. Strict validation
                if f_path:
                    # Check if it matches the codebase root (directory)
                    if f_path == self.codebase_path:
                        logger.warning(f"Skipping codebase root directory in file list: {f_path}")
                        continue
                    
                    if f_path.is_file():
                        # Update absolute_path in entry to be safe for downstream
                        entry["absolute_path"] = str(f_path)
                        valid_files.append(entry)
                    elif f_path.is_dir():
                        logger.warning(f"Skipping directory inadvertently included: {f_path}")
                    elif not f_path.exists():
                        logger.warning(f"Skipping missing file: {f_path}")
                else:
                    # No resolvable path found in entry, skip to be safe
                    logger.warning(f"Skipping entry with no resolvable path: {entry}")
            
            self.file_cache = valid_files
            # ----------------------------------------------------------------

            if self.verbose and self.console:
                self.console.print(f"[green]Discovered {len(self.file_cache)} files[/green]")

            return self.file_cache

        except Exception as e:
            error_msg = f"File discovery failed: {str(e)}"
            logger.error(error_msg)
            self.errors.append({
                "stage": "discover_and_cache_files",
                "error": error_msg
            })
            return []

    # ========================================================================
    # PHASE 2: BATCH ANALYSIS WITH MEMORY MANAGEMENT
    # ========================================================================

    def _get_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        if not PSUTIL_AVAILABLE:
            return 0.0
        try:
            import psutil
            return psutil.Process().memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0

    def _analyze_in_batches(self) -> None:
        """
        Run all analyzers on files in memory-efficient batches.

        Splits file_cache into batches, monitors memory, and forces GC
        if memory exceeds limit.
        """
        if not self.file_cache:
            logger.warning("No files to analyze")
            return

        if self.verbose and self.console:
            self.console.print("[bold cyan]Phase 2: Batch analysis with memory management...[/bold cyan]")

        total_batches = (len(self.file_cache) + self.batch_size - 1) // self.batch_size

        for batch_idx in range(0, len(self.file_cache), self.batch_size):
            batch = self.file_cache[batch_idx : batch_idx + self.batch_size]
            current_batch_num = (batch_idx // self.batch_size) + 1

            if self.verbose and self.console:
                self.console.print(
                    f"Processing batch {current_batch_num}/{total_batches} "
                    f"({len(batch)} files)"
                )

            # Monitor memory
            mem_mb = self._get_memory_mb()
            if mem_mb > self.memory_limit_mb:
                if self.verbose and self.console:
                    self.console.print(
                        f"[yellow]Memory usage ({mem_mb:.1f} MB) exceeds limit "
                        f"({self.memory_limit_mb} MB), forcing GC[/yellow]"
                    )
                gc.collect()

            # Note: Per-file analyzer results would be populated into batch entries
            # by specialized analyzers. This phase mainly manages batching and memory.

        if self.verbose and self.console:
            self.console.print("[green]Batch analysis complete[/green]")

    # ========================================================================
    # PHASE 3: DEPENDENCY GRAPH BUILDING
    # ========================================================================

    def build_dependency_graph(self) -> Dict[str, Any]:
        """
        Build dependency graph using DependencyAnalyzer.

        Returns:
            Dependency graph dictionary
        """
        if not DEPENDENCY_ANALYZER_AVAILABLE or self.dependency_analyzer is None:
            logger.error("DependencyAnalyzer not available")
            self.errors.append({
                "stage": "build_dependency_graph",
                "error": "DependencyAnalyzer not available"
            })
            return {}

        if not self.file_cache:
            logger.error("No file cache; run discover_and_cache_files first")
            self.errors.append({
                "stage": "build_dependency_graph",
                "error": "Empty file cache"
            })
            return {}

        try:
            if self.verbose and self.console:
                self.console.print("[bold cyan]Phase 3: Building dependency graph...[/bold cyan]")

            self.dependency_graph = self.dependency_analyzer.build_graph(self.file_cache)

            if self.verbose and self.console:
                nodes = self.dependency_graph.get("nodes", [])
                edges = self.dependency_graph.get("edges", [])
                self.console.print(
                    f"[green]Dependency graph built: {len(nodes)} nodes, "
                    f"{len(edges)} edges[/green]"
                )

            return self.dependency_graph

        except Exception as e:
            error_msg = f"Dependency graph building failed: {str(e)}"
            logger.error(error_msg)
            self.errors.append({
                "stage": "build_dependency_graph",
                "error": error_msg
            })
            return {}

    # ========================================================================
    # PHASE 4: HEALTH METRICS CALCULATION
    # ========================================================================

    def calculate_health_metrics(self) -> Dict[str, Any]:
        """
        Calculate all 9 health metrics using MetricsCalculator.

        Returns:
            Health metrics dictionary with scores for all 9 dimensions
        """
        if not METRICS_CALCULATOR_AVAILABLE or self.metrics_calculator is None:
            logger.error("MetricsCalculator not available")
            self.errors.append({
                "stage": "calculate_health_metrics",
                "error": "MetricsCalculator not available"
            })
            return {}

        if not self.file_cache:
            logger.error("No file cache; run discover_and_cache_files first")
            self.errors.append({
                "stage": "calculate_health_metrics",
                "error": "Empty file cache"
            })
            return {}

        try:
            if self.verbose and self.console:
                self.console.print("[bold cyan]Phase 4: Calculating health metrics...[/bold cyan]")

            self.health_metrics = self.metrics_calculator.calculate_all_metrics(
                self.file_cache, self.dependency_graph
            )

            # ── HITL: filter adapter results based on human feedback ─
            if self.hitl_context and self.health_metrics:
                adapters = self.health_metrics.get("adapters", {})
                for adapter_name, adapter_data in adapters.items():
                    if isinstance(adapter_data, dict) and "details" in adapter_data:
                        original_count = len(adapter_data["details"])
                        adapter_data["details"] = [
                            d for d in adapter_data["details"]
                            if not self.hitl_context.should_skip_issue(
                                d.get("category", adapter_name),
                                d.get("file", ""),
                            )
                        ]
                        filtered = original_count - len(adapter_data["details"])
                        if filtered > 0:
                            logger.info(
                                "HITL: filtered %d issues from %s adapter",
                                filtered, adapter_name,
                            )

            if self.verbose and self.console:
                overall = self.health_metrics.get("overall_health", {})
                score = overall.get("score", 0)
                grade = overall.get("grade", "N/A")
                self.console.print(
                    f"[green]Health metrics calculated: "
                    f"overall score {score}/100 (grade {grade})[/green]"
                )

            return self.health_metrics

        except Exception as e:
            error_msg = f"Health metrics calculation failed: {str(e)}"
            logger.error(error_msg)
            self.errors.append({
                "stage": "calculate_health_metrics",
                "error": error_msg
            })
            return {}

    # ========================================================================
    # PHASE 5: LLM ENRICHMENT
    # ========================================================================

    def _chunk_list(self, items: List[Any], chunk_size: int) -> List[List[Any]]:
        """Split list into chunks of specified size."""
        if not items or chunk_size <= 0:
            return [items] if items else []
        return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

    def _extract_json_from_response(self, response: str) -> Dict[str, Any]:
        """
        Extract JSON from LLM response, handling ```json blocks.

        Args:
            response: LLM response string

        Returns:
            Parsed JSON dictionary
        """
        if not response:
            return {}

        try:
            # Try to find JSON in ```json ... ``` blocks
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    return {"raw_response": response}

            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON from response: {e}")
            return {"parse_error": str(e), "raw_response": response}
        except Exception as e:
            logger.warning(f"Error extracting JSON: {e}")
            return {"error": str(e)}

    def _prepare_metrics_summary_for_llm(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare compact summary of metrics for LLM prompt.

        Truncates large arrays to avoid token explosion.

        Args:
            metrics: Full metrics dictionary

        Returns:
            Compact metrics summary
        """
        summary = {}

        for key, value in metrics.items():
            if isinstance(value, dict):
                # Keep score, grade, and truncate issues
                summary[key] = {
                    "score": value.get("score", 0),
                    "grade": value.get("grade", "N/A"),
                    "issues_count": len(value.get("issues", [])),
                }
            elif isinstance(value, list):
                summary[key] = f"[{len(value)} items]"
            else:
                summary[key] = value

        return summary

    def _llm_batch_process(
        self,
        items: List[Any],
        chunk_size: int,
        build_prompt_fn,
        parse_fn,
    ) -> Any:
        """
        Process items through LLM in chunks.

        Args:
            items: Items to process
            chunk_size: Chunk size for LLM
            build_prompt_fn: Function to build prompt from chunk
            parse_fn: Function to parse LLM response

        Returns:
            Accumulated results from all chunks
        """
        if not self.llm_tools:
            return None

        result = None
        chunks = self._chunk_list(items, chunk_size)

        for idx, chunk in enumerate(chunks):
            if not chunk:
                continue

            try:
                if self.verbose and self.console:
                    self.console.print(f"Processing LLM chunk {idx + 1}/{len(chunks)}")

                prompt = build_prompt_fn(chunk)
                response = self.llm_tools.llm_call(prompt)
                partial = parse_fn(response)

                if result is None:
                    result = partial
                else:
                    # Merge partial results
                    if isinstance(result, dict) and isinstance(partial, dict):
                        result.update(partial)
                    elif isinstance(result, list) and isinstance(partial, list):
                        result.extend(partial)

            except Exception as e:
                logger.warning(f"LLM batch processing failed for chunk {idx}: {e}")
                self.errors.append({
                    "stage": "llm_batch_process",
                    "chunk_idx": idx,
                    "error": str(e)
                })

        return result

    def enrich_with_llm(self) -> Dict[str, Any]:
        """
        Optional LLM enrichment of analysis results.

        Uses PromptTemplates to generate prompts for each analysis stage
        and calls LLM for intelligent insights.

        Returns:
            Dictionary with LLM enrichment results
        """
        if not self.enable_llm or not self.llm_tools:
            return {"status": "LLM enrichment disabled"}

        if not PROMPT_TEMPLATES_AVAILABLE:
            logger.warning("PromptTemplates not available")
            return {"status": "PromptTemplates not available"}

        if self.verbose and self.console:
            self.console.print("[bold cyan]Phase 5: LLM enrichment...[/bold cyan]")

        try:
            pt = PromptTemplates()
            enrichment = {}

            # 1. Codebase insights
            if self.file_cache:
                try:
                    prompt = pt.get_codebase_insights_prompt(self.file_cache)
                    response = self.llm_tools.llm_call(prompt)
                    enrichment["codebase_insights"] = self._extract_json_from_response(response)
                    if self.verbose and self.console:
                        self.console.print("[green]✓ Codebase insights[/green]")
                except Exception as e:
                    logger.warning(f"Codebase insights LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_codebase_insights",
                        "error": str(e)
                    })

            # 2. Dependency analysis
            if self.dependency_graph:
                try:
                    prompt = pt.get_dependency_analysis_prompt(self.dependency_graph)
                    response = self.llm_tools.llm_call(prompt)
                    enrichment["dependency_analysis"] = self._extract_json_from_response(response)
                    if self.verbose and self.console:
                        self.console.print("[green]✓ Dependency analysis[/green]")
                except Exception as e:
                    logger.warning(f"Dependency analysis LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_dependency_analysis",
                        "error": str(e)
                    })

            # 3. Health metrics analysis
            if self.health_metrics:
                try:
                    metrics_summary = self._prepare_metrics_summary_for_llm(self.health_metrics)
                    prompt = pt.get_health_metrics_analysis_prompt(metrics_summary)
                    if self.hitl_context:
                        prompt = self.hitl_context.augment_prompt(
                            original_prompt=prompt,
                            issue_type="health_metrics",
                            file_path=str(self.codebase_path),
                            agent_type="static_analyzer",
                        )
                    response = self.llm_tools.llm_call(prompt)
                    enrichment["health_metrics_analysis"] = self._extract_json_from_response(response)
                    if self.verbose and self.console:
                        self.console.print("[green]✓ Health metrics analysis[/green]")
                except Exception as e:
                    logger.warning(f"Health metrics analysis LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_health_metrics",
                        "error": str(e)
                    })

            # 4. Documentation recommendations
            if self.dependency_graph:
                try:
                    if DEPENDENCY_ANALYZER_AVAILABLE and self.dependency_analyzer:
                        self.documentation = self.dependency_analyzer.document_graph(self.dependency_graph)
                        prompt = pt.get_documentation_recommendations_prompt(self.documentation)
                        response = self.llm_tools.llm_call(prompt)
                        enrichment["documentation_recommendations"] = self._extract_json_from_response(response)
                        if self.verbose and self.console:
                            self.console.print("[green]✓ Documentation recommendations[/green]")
                except Exception as e:
                    logger.warning(f"Documentation recommendations LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_documentation",
                        "error": str(e)
                    })

            # 5. Modularization plan
            if self.dependency_graph:
                try:
                    if DEPENDENCY_ANALYZER_AVAILABLE and self.dependency_analyzer:
                        self.modularization_plan = self.dependency_analyzer.propose_modularization(
                            self.dependency_graph
                        )
                        prompt = pt.get_modularization_plan_prompt(self.dependency_graph, self.modularization_plan)
                        response = self.llm_tools.llm_call(prompt)
                        enrichment["modularization_plan"] = self._extract_json_from_response(response)
                        if self.verbose and self.console:
                            self.console.print("[green]✓ Modularization plan[/green]")
                except Exception as e:
                    logger.warning(f"Modularization plan LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_modularization",
                        "error": str(e)
                    })

            # 6. Validation insights
            if self.dependency_graph and self.modularization_plan:
                try:
                    if DEPENDENCY_ANALYZER_AVAILABLE and self.dependency_analyzer:
                        self.validation_report = self.dependency_analyzer.validate_modularization(
                            self.dependency_graph, self.modularization_plan
                        )
                        prompt = pt.get_validation_insights_prompt(self.validation_report, self.modularization_plan)
                        response = self.llm_tools.llm_call(prompt)
                        enrichment["validation_insights"] = self._extract_json_from_response(response)
                        if self.verbose and self.console:
                            self.console.print("[green]✓ Validation insights[/green]")
                except Exception as e:
                    logger.warning(f"Validation insights LLM call failed: {e}")
                    self.errors.append({
                        "stage": "enrich_llm_validation",
                        "error": str(e)
                    })

            self.llm_enrichment = enrichment
            return enrichment

        except Exception as e:
            error_msg = f"LLM enrichment failed: {str(e)}"
            logger.error(error_msg)
            self.errors.append({
                "stage": "enrich_with_llm",
                "error": error_msg
            })
            return {}

    # ========================================================================
    # PHASE 6: REPORT GENERATION
    # ========================================================================

    def generate_reports(self) -> Dict[str, str]:
        """
        Generate health report in JSON, Excel, and optionally email.

        Returns:
            Dictionary mapping report type to file path
        """
        if self.verbose and self.console:
            self.console.print("[bold cyan]Phase 6: Generating reports...[/bold cyan]")

        # Build unified health report
        health_report = {
            "version": "2.0",
            "generated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "codebase_path": str(self.codebase_path),
                "total_files": len(self.file_cache),
                "file_extensions": self.file_extensions,
            },
            "summary": {
                "file_stats": {
                    "total_files": len(self.file_cache),
                    "file_types": dict(Counter([
                        Path(f.get("file_relative_path", "")).suffix
                        for f in self.file_cache
                    ])),
                }
            },
            "dependency_graph": self.dependency_graph,
            "documentation": self.documentation,
            "modularization_plan": self.modularization_plan,
            "validation_report": self.validation_report,
            "health_metrics": self.health_metrics,
            "llm_analysis": self.llm_enrichment,
            "file_cache": self.file_cache,
            "errors": self.errors,
        }

        # Write health_report.json
        health_report_path = self.output_dir / "health_report.json"
        try:
            with open(health_report_path, 'w', encoding='utf-8') as f:
                json.dump(health_report, f, indent=2, default=str)
            self.report_files["json"] = str(health_report_path)
            if self.verbose and self.console:
                self.console.print(f"[green]✓ JSON report: {health_report_path}[/green]")
        except Exception as e:
            logger.error(f"Failed to write JSON report: {e}")
            self.errors.append({
                "stage": "generate_reports_json",
                "error": str(e)
            })

        # Write individual metric JSONs
        for metric_name, metric_data in self.health_metrics.items():
            try:
                metric_path = self.output_dir / f"{metric_name}.json"
                with open(metric_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "metric_name": metric_name,
                        "data": metric_data,
                        "generated_at": datetime.utcnow().isoformat(),
                    }, f, indent=2, default=str)
            except Exception as e:
                logger.warning(f"Failed to write metric {metric_name}: {e}")

        # Write Excel report
        if EXCEL_WRITER_AVAILABLE:
            try:
                excel_path = self.output_dir / "health_report.xlsx"
                writer = ExcelWriter(str(excel_path))

                # Summary sheet
                writer.add_data_sheet(
                    health_report["metadata"],
                    "Metadata",
                    "Health Report"
                )

                # Health metrics sheet
                if self.health_metrics:
                    metrics_data = []
                    for metric_name, metric_data in self.health_metrics.items():
                        if isinstance(metric_data, dict):
                            metrics_data.append({
                                "Metric": metric_name,
                                "Score": metric_data.get("score", 0),
                                "Grade": metric_data.get("grade", "N/A"),
                                "Issues": len(metric_data.get("issues", [])),
                            })
                    if metrics_data:
                        headers = ["Metric", "Score", "Grade", "Issues"]
                        writer.add_table_sheet(
                            headers, metrics_data, "Metrics", status_column="Grade"
                        )

                # File summary sheet
                if self.file_cache:
                    files_data = []
                    for f in self.file_cache[:100]:  # Limit to 100 files for readability
                        files_data.append({
                            "File": f.get("file_relative_path", "unknown"),
                            "Language": f.get("language", "unknown"),
                            "Lines": f.get("metrics", {}).get("total_lines", 0),
                        })
                    if files_data:
                        headers = ["File", "Language", "Lines"]
                        writer.add_table_sheet(headers, files_data, "Files")

                writer.save()
                self.report_files["excel"] = str(excel_path)
                if self.verbose and self.console:
                    self.console.print(f"[green]✓ Excel report: {excel_path}[/green]")
            except Exception as e:
                logger.warning(f"Failed to write Excel report: {e}")
                self.errors.append({
                    "stage": "generate_reports_excel",
                    "error": str(e)
                })

        # Send email if configured
        try:
            if GLOBAL_CONFIG_AVAILABLE and self.config:
                email_recipients = self.config.get_list("email.recipients", [])
                if email_recipients and EMAIL_REPORTER_AVAILABLE:
                    try:
                        reporter = EmailReporter()
                        stats = {
                            "Total Files": len(self.file_cache),
                            "Overall Score": self.health_metrics.get("overall_health", {}).get("score", 0),
                        }
                        reporter.send_report(
                            recipients=email_recipients,
                            metadata=health_report["metadata"],
                            stats=stats,
                            analysis_summary="Static analysis completed. See attached reports.",
                            attachment_path=self.report_files.get("excel", ""),
                        )
                        self.report_files["email_sent"] = "true"
                        if self.verbose and self.console:
                            self.console.print(f"[green]✓ Email sent to {email_recipients}[/green]")
                    except Exception as e:
                        logger.warning(f"Failed to send email: {e}")
        except Exception as e:
            logger.warning(f"Email configuration check failed: {e}")

        return self.report_files

    # ========================================================================
    # PHASE 7: VISUALIZATION
    # ========================================================================

    def generate_visualizations(self) -> Dict[str, str]:
        """
        Generate Mermaid dependency diagram and other visualizations.

        Returns:
            Dictionary mapping visualization name to file path
        """
        if not GRAPH_GENERATOR_AVAILABLE:
            logger.warning("GraphGenerator not available")
            return {}

        if self.verbose and self.console:
            self.console.print("[bold cyan]Phase 7: Generating visualizations...[/bold cyan]")

        try:
            gen = GraphGenerator()
            files_map = gen.generate_all_visualizations(
                self.dependency_graph,
                self.modularization_plan,
                self.health_metrics,
                str(self.output_dir),
            )
            self.visualizations = files_map or {}
            if self.verbose and self.console:
                self.console.print(f"[green]Generated {len(self.visualizations)} visualizations[/green]")
            return self.visualizations

        except Exception as e:
            logger.warning(f"Visualization generation failed: {e}")
            self.errors.append({
                "stage": "generate_visualizations",
                "error": str(e)
            })
            return {}

    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================

    def run_analysis(self) -> Dict[str, Any]:
        """
        Execute the complete 7-phase analysis pipeline.

        Returns:
            Structured dictionary with analysis results:
            - status: "success", "partial", or "error"
            - health_report_path: Path to generated JSON report
            - health_metrics: Dictionary of all health metrics
            - dependency_graph: Built dependency graph
            - file_cache: List of processed files
            - summary: Analysis summary
            - documentation: Generated documentation
            - modularization_plan: Proposed modularization
            - validation_report: Validation results
            - final_report: Comprehensive final report
            - report_files: Paths to all generated reports
            - visualizations: Paths to generated visualizations
            - errors: List of errors encountered
        """
        try:
            if self.verbose and self.console:
                self.console.print(
                    "[bold magenta]Starting 7-phase static analysis pipeline[/bold magenta]"
                )

            # Phase 1: File Discovery
            self.discover_and_cache_files()
            if not self.file_cache:
                return {
                    "status": "error",
                    "errors": self.errors,
                    "message": "No files discovered",
                }

            # Phase 2: Batch Analysis
            self._analyze_in_batches()

            # Phase 3: Dependency Graph
            self.build_dependency_graph()

            # Phase 4: Health Metrics
            self.calculate_health_metrics()

            # Phase 5: LLM Enrichment
            self.enrich_with_llm()

            # Phase 6: Report Generation
            self.generate_reports()

            # Phase 7: Visualization
            self.generate_visualizations()

            # Build final comprehensive report
            self.final_report = {
                "summary": {
                    "total_files": len(self.file_cache),
                    "analysis_timestamp": datetime.utcnow().isoformat(),
                },
                "dependency_graph": self.dependency_graph,
                "documentation": self.documentation,
                "modularization_plan": self.modularization_plan,
                "validation_report": self.validation_report,
                "health_metrics": self.health_metrics,
                "llm_analysis": self.llm_enrichment,
            }

            status = "success" if not self.errors else "partial"

            if self.verbose and self.console:
                self.console.print(
                    f"[bold green]Analysis complete with status: {status}[/bold green]"
                )

            return {
                "status": status,
                "health_report_path": str(self.output_dir / "health_report.json"),
                "health_metrics": self.health_metrics,
                "dependency_graph": self.dependency_graph,
                "file_cache": self.file_cache,
                "summary": {
                    "total_files": len(self.file_cache),
                    "file_types": dict(Counter([
                        Path(f.get("file_relative_path", "")).suffix
                        for f in self.file_cache
                    ])),
                },
                "documentation": self.documentation,
                "modularization_plan": self.modularization_plan,
                "validation_report": self.validation_report,
                "final_report": self.final_report,
                "report_files": self.report_files,
                "adapter_results": self.health_metrics.get("adapters", {}),
                "detailed_code_review_path": str(
                    self.output_dir / "detailed_code_review.xlsx"
                ) if self.health_metrics.get("adapters") else None,
                "visualizations": self.visualizations,
                "errors": self.errors,
            }

        except Exception as e:
            error_msg = f"Analysis pipeline failed: {str(e)}"
            logger.error(error_msg)
            self.errors.append({
                "stage": "run_analysis",
                "error": error_msg
            })
            return {
                "status": "error",
                "errors": self.errors,
                "message": error_msg,
            }

    def get_results(self) -> Dict[str, Any]:
        """
        Return structured results for multi-agent workflow orchestration.

        Returns:
            Results dictionary compatible with orchestration frameworks
        """
        return {
            "agent": self.__class__.__name__,
            "status": "success" if not self.errors else "error",
            "results": {
                "health_metrics": self.health_metrics,
                "dependency_graph": self.dependency_graph,
                "file_cache": self.file_cache,
                "documentation": self.documentation,
                "modularization_plan": self.modularization_plan,
                "validation_report": self.validation_report,
                "llm_enrichment": self.llm_enrichment,
                "adapter_results": self.health_metrics.get("adapters", {}),
                "report_files": self.report_files,
                "visualizations": self.visualizations,
            },
            "errors": self.errors,
            "metadata": {
                "codebase_path": str(self.codebase_path),
                "total_files": len(self.file_cache),
                "timestamp": datetime.utcnow().isoformat(),
            },
        }


# ============================================================================
# MAIN ENTRY POINT FOR CLI/SCRIPT USE
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python codebase_static_agent.py <codebase_path> [output_dir]")
        sys.exit(1)

    codebase = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "./analysis_output"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    agent = StaticAnalyzerAgent(
        codebase_path=codebase,
        output_dir=output,
        enable_llm=True,
        verbose=True,
    )

    results = agent.run_analysis()

    print("\n" + "=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)
    print(json.dumps({
        "status": results.get("status"),
        "report_files": results.get("report_files"),
        "visualizations": results.get("visualizations"),
        "errors_count": len(results.get("errors", [])),
    }, indent=2))