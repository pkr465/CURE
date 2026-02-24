import fnmatch
import os
import re
import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# --- Imports ---
from utils.common.llm_tools import LLMTools, LLMConfig
from utils.common.email_reporter import EmailReporter
from utils.common.excel_writer import ExcelWriter, ExcelStyle
from utils.parsers.global_config_parser import GlobalConfig

# --- New Ingestion & Dependency Services ---
try:
    from dependency_builder.ccls_ingestion import CCLSIngestion
    from dependency_builder.dependency_service import DependencyService
    from dependency_builder.config import DependencyBuilderConfig
    DEPENDENCY_SERVICES_AVAILABLE = True
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("Ingestion/Dependency services not found. Running in standalone heuristic mode.")
    CCLSIngestion = None
    DependencyService = None
    DependencyBuilderConfig = None
    DEPENDENCY_SERVICES_AVAILABLE = False

# Try importing the prompt
try:
    from prompts.codebase_analysis_prompt import CODEBASE_ANALYSIS_PROMPT
except ImportError:
    CODEBASE_ANALYSIS_PROMPT = "Error: Prompt file not found."

# HITL support (optional)
try:
    from hitl import HITLContext, HITL_AVAILABLE
except ImportError:
    HITLContext = None
    HITL_AVAILABLE = False

# Header context builder (for context-aware LLM analysis)
try:
    from agents.context.header_context_builder import HeaderContextBuilder
    HEADER_CONTEXT_AVAILABLE = True
except ImportError:
    HeaderContextBuilder = None
    HEADER_CONTEXT_AVAILABLE = False

# Context validator (per-chunk false positive reduction)
try:
    from agents.context.context_validator import ContextValidator
    CONTEXT_VALIDATOR_AVAILABLE = True
except ImportError:
    ContextValidator = None
    CONTEXT_VALIDATOR_AVAILABLE = False

# Static call stack analyzer (cross-function call chain tracing)
try:
    from agents.context.static_call_stack_analyzer import StaticCallStackAnalyzer
    CALL_STACK_ANALYZER_AVAILABLE = True
except ImportError:
    StaticCallStackAnalyzer = None
    CALL_STACK_ANALYZER_AVAILABLE = False

# Configure logging
logger = logging.getLogger(__name__)


class CodebaseLLMAgent:
    """
    LLM-Enhanced Agent for strict C/C++ code review.

    UPDATED ARCHITECTURE:
    1. Ingestion Phase: Runs CCLS indexing to build a dependency graph (Optional via --use-ccls).
    2. Semantic Chunking: Combines physical code blocks with dependency context.
    3. Anchor Logic: Maps LLM findings back to exact source lines.
    4. Constraint Injection: Loads 'Issue Identification Rules' from constraints files to guide LLM.

    MODERNIZED IMPLEMENTATION:
    - Dependency injection for LLMTools, config, and DependencyBuilderConfig
    - Uses EmailReporter (not CodebaseEmailReporter)
    - Uses ExcelWriter (not xlsxwriter directly)
    - Proper logging instead of print statements
    - Configuration-driven email recipients
    """

    C_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}

    # Chunking Config
    TARGET_CHUNK_CHARS = 12000  # ~3k tokens
    OVERLAP_LINES = 25

    def __init__(
        self,
        codebase_path: str,
        output_dir: str = "./out",
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
        max_files: int = 10000,
        use_ccls: bool = False,
        file_to_fix: Optional[str] = None,
        config: Optional[GlobalConfig] = None,
        llm_tools: Optional[LLMTools] = None,
        dep_config: Optional[DependencyBuilderConfig] = None,
        hitl_context: Optional['HITLContext'] = None,
        constraints_dir: str = "agents/constraints",
        custom_constraints: Optional[List[str]] = None,
    ):
        """
        Initialize CodebaseLLMAgent with dependency injection support.

        :param codebase_path: Root of the C/C++ project.
        :param output_dir: Directory for reports and CCLS cache.
        :param exclude_dirs: Directories to exclude from scanning.
        :param exclude_globs: Glob patterns to exclude (relative paths, fnmatch syntax).
        :param max_files: Maximum number of files to analyze.
        :param use_ccls: Boolean to enable/disable CCLS dependency analysis.
        :param file_to_fix: Specific relative path to a file to analyze (ignores others).
        :param config: Optional GlobalConfig for configuration management.
        :param llm_tools: Optional pre-configured LLMTools (for multi-agent sharing).
        :param dep_config: Optional DependencyBuilderConfig for dependency services.
        :param hitl_context: Optional HITLContext for human-in-the-loop feedback integration.
        :param constraints_dir: Path to the constraints directory (default: agents/constraints).
        :param custom_constraints: Additional custom constraint .md file paths to include.
        """
        self.config = config or GlobalConfig()
        self.codebase_path = Path(codebase_path).resolve()

        # --- PATH FIX: Resolve output_dir relative to codebase_path if relative ---
        if os.path.isabs(output_dir):
            self.output_dir = output_dir
        else:
            clean_out = output_dir.replace("./", "").strip("/")
            if not clean_out:
                clean_out = "out"
            self.output_dir = output_dir

        self.project_name = self.codebase_path.name
        self.max_files = max_files
        self.run_id = str(uuid.uuid4())
        self.start_time = datetime.now()
        self.use_ccls = use_ccls
        self.file_to_fix = file_to_fix

        # Constraint Directory Setup
        self.constraints_dir = Path(constraints_dir)
        if not self.constraints_dir.is_absolute():
            # Attempt to resolve relative to CWD first, then fallback to script location
            if not self.constraints_dir.exists():
                # Fallback: check relative to this script's directory
                script_dir = Path(__file__).parent.resolve()
                potential_dir = script_dir / constraints_dir
                if potential_dir.exists():
                    self.constraints_dir = potential_dir

        self.exclude_dirs = set(exclude_dirs or [
            ".git", "build", "dist", ".idea", ".vscode",
            "node_modules", "third_party", "__pycache__", ".ccls-cache"
        ])
        self.exclude_globs = exclude_globs or []
        self.custom_constraints = custom_constraints or []

        # --- Dependency Injection: LLMTools ---
        if llm_tools:
            self.llm_tools = llm_tools
        elif self.config:
            model = self.config.get("llm.model")
            if model:
                self.llm_tools = LLMTools(model=model)
            else:
                self.llm_tools = LLMTools()
        else:
            self.llm_tools = LLMTools()

        self.results: List[Dict] = []
        self.errors: List[Dict] = []

        # --- Initialize Services based on Flag AND Availability ---
        if self.use_ccls:
            if DEPENDENCY_SERVICES_AVAILABLE:
                logger.info("[*] CCLS Dependency Services ENABLED.")
                dep_cfg = dep_config or (
                    DependencyBuilderConfig.from_env() if DependencyBuilderConfig else None
                )
                self.ingestion = CCLSIngestion(config=dep_cfg) if dep_cfg else CCLSIngestion()
                self.dep_service = DependencyService(config=dep_cfg) if dep_cfg else DependencyService()
            else:
                logger.warning(
                    "[!] Warning: --use-ccls requested but dependency services are not installed. "
                    "Reverting to heuristic mode."
                )
                self.ingestion = None
                self.dep_service = None
        else:
            logger.info("[*] CCLS Dependency Services DISABLED (heuristic mode).")
            self.ingestion = None
            self.dep_service = None

        self.is_indexed = False
        self.hitl_context = hitl_context

        # --- Header Context Builder (context-aware analysis) ---
        self.header_context_builder = None
        context_cfg = self.config.get("context", {}) if self.config else {}
        if isinstance(context_cfg, dict) and context_cfg.get("enable_header_context", True):
            if HEADER_CONTEXT_AVAILABLE:
                try:
                    inc_paths = context_cfg.get("include_paths", [])
                    self.header_context_builder = HeaderContextBuilder(
                        codebase_path=str(self.codebase_path),
                        include_paths=inc_paths if isinstance(inc_paths, list) else [],
                        max_header_depth=int(context_cfg.get("max_header_depth", 2)),
                        max_context_chars=int(context_cfg.get("max_context_chars", 6000)),
                        exclude_system_headers=context_cfg.get("exclude_system_headers", True),
                        exclude_dirs=list(self.exclude_dirs),
                        exclude_globs=self.exclude_globs,
                    )
                    logger.info(
                        f"[*] Header Context Builder ENABLED "
                        f"(codebase_path={self.codebase_path}, "
                        f"include_paths={inc_paths})"
                    )
                except Exception as hcb_err:
                    logger.warning(f"Failed to initialize HeaderContextBuilder: {hcb_err}")
            else:
                logger.debug("HeaderContextBuilder not available (import failed).")

        # --- Context Validator (per-chunk false positive reduction) ---
        self.context_validator = None
        if CONTEXT_VALIDATOR_AVAILABLE:
            try:
                self.context_validator = ContextValidator(
                    codebase_path=str(self.codebase_path),
                    use_ccls=self.use_ccls and self.is_indexed,
                    ccls_navigator=None,  # Will be updated after CCLS indexing
                )
                logger.info("[*] Context Validator ENABLED (heuristic mode)")
            except Exception as cv_err:
                logger.debug(f"Context Validator init failed: {cv_err}")

        # --- Static Call Stack Analyzer (cross-function call chain tracing) ---
        self.call_stack_analyzer = None
        if CALL_STACK_ANALYZER_AVAILABLE:
            try:
                _csa_cache_dir = os.path.join(self.output_dir, ".cache")
                self.call_stack_analyzer = StaticCallStackAnalyzer(
                    codebase_path=str(self.codebase_path),
                    exclude_dirs=list(self.exclude_dirs),
                    exclude_globs=self.exclude_globs,
                    header_context_builder=self.header_context_builder,
                    use_ccls=self.use_ccls,
                    ccls_navigator=None,  # Updated after CCLS indexing
                    max_trace_depth=3,
                    max_context_chars=1200,
                    cache_dir=_csa_cache_dir,
                )
                logger.info(
                    f"[*] Call Stack Analyzer ENABLED "
                    f"({self.call_stack_analyzer.index.stats()})"
                )
            except Exception as csa_err:
                logger.warning(f"Call Stack Analyzer init failed: {csa_err}")

    def _extract_constraint_section(self, content: str, keyword: str) -> str:
        """
        Parses Markdown content to find a header containing the keyword (e.g., 'Issue Identification Rules')
        and extracts the text until the next header.
        
        Args:
            content: The raw markdown content of the constraint file.
            keyword: The section header keyword to search for.
            
        Returns:
            The extracted text content of that section, or empty string if not found.
        """
        try:
            # Regex: Find '## ... keyword ...' then capture content until next '## ' or End of String
            # Using re.IGNORECASE to match headers regardless of casing
            pattern = re.compile(
                r"^## .*?" + re.escape(keyword) + r".*?$\n(.*?)(?=^## |\Z)", 
                re.MULTILINE | re.DOTALL | re.IGNORECASE
            )
            match = pattern.search(content)
            if match:
                return match.group(1).strip()
            return ""
        except Exception as e:
            logger.warning(f"Failed to extract section '{keyword}': {e}")
            return ""

    def _load_constraints(self, file_path: str, section_keyword: str = "Issue Identification Rules") -> str:
        """
        Loads constraints from:
        1. agents/constraints/common_constraints.md 
        2. A recursive search for <filename>_constraints.md inside agents/constraints/
        
        Specifically extracts the section matching 'section_keyword' (default: 'Issue Identification Rules').
        
        :param file_path: The relative path or filename of the code being analyzed.
        :param section_keyword: The header keyword to look for in the markdown files.
        :return: A combined string of constraints to inject into the prompt.
        """
        combined_constraints = []
        
        # Ensure self.constraints_dir is a Path object
        base_dir = Path(self.constraints_dir)
        
        # ---------------------------------------------------------
        # 1. Load Common Constraints
        # ---------------------------------------------------------
        common_file = base_dir / "common_constraints.md"
        
        if common_file.exists():
            try:
                with open(common_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    section_content = self._extract_constraint_section(content, section_keyword)
                    if section_content:
                        combined_constraints.append(f"--- GLOBAL IDENTIFICATION RULES ---\n{section_content}\n")
            except Exception as e:
                logger.warning(f"Failed to read common constraints at {common_file}: {e}")

        # ---------------------------------------------------------
        # 1b. Load Auto-Generated Codebase Constraints
        # ---------------------------------------------------------
        codebase_file = base_dir / "codebase_constraints.md"
        if codebase_file.exists():
            try:
                with open(codebase_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    section_content = self._extract_constraint_section(content, section_keyword)
                    if section_content:
                        combined_constraints.append(f"--- AUTO-GENERATED CODEBASE RULES ---\n{section_content}\n")
                        logger.info(f"    > Loaded auto-generated codebase constraints: {codebase_file}")
            except Exception as e:
                logger.warning(f"Failed to read codebase constraints at {codebase_file}: {e}")

        # ---------------------------------------------------------
        # 1c. Load Custom Constraint Files (user-provided paths)
        # ---------------------------------------------------------
        for custom_path in self.custom_constraints:
            cpath = Path(custom_path)
            if not cpath.is_absolute():
                # Try relative to CWD, then relative to constraints_dir
                if not cpath.exists():
                    cpath = base_dir / custom_path
            if cpath.exists():
                try:
                    with open(cpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        section_content = self._extract_constraint_section(content, section_keyword)
                        if section_content:
                            combined_constraints.append(
                                f"--- CUSTOM RULES ({cpath.name}) ---\n{section_content}\n"
                            )
                            logger.info(f"    > Loaded custom constraints: {cpath}")
                except Exception as e:
                    logger.warning(f"Failed to read custom constraints at {cpath}: {e}")
            else:
                logger.warning(f"Custom constraint file not found: {custom_path}")

        # ---------------------------------------------------------
        # 2. Load File-Specific Constraints (Recursive Search)
        # ---------------------------------------------------------
        # Create a Path object from the input string to handle parsing easily
        target_path = Path(file_path)
        
        # .stem returns the filename without the last extension (e.g., 'ratectrl.c' -> 'ratectrl')
        target_name_no_ext = target_path.stem
        
        # Construct the search pattern: e.g., "ratectrl_constraints.md"
        search_filename = f"{target_name_no_ext}_constraints.md"
        
        specific_file_path = None
        
        try:
            # rglob('*') recursively searches all subdirectories
            # We search specifically for the filename we constructed
            found_files = list(base_dir.rglob(search_filename))
            
            if len(found_files) == 1:
                specific_file_path = found_files[0]
            elif len(found_files) > 1:
                # If multiple files match (e.g., in different subfolders), pick the first but warn
                specific_file_path = found_files[0]
                logger.warning(f"Multiple constraint files found for {search_filename}. Using: {specific_file_path}")
                
        except Exception as e:
            logger.error(f"Error while searching for specific constraints: {e}")

        # If we found a file, read it
        if specific_file_path and specific_file_path.exists():
            try:
                with open(specific_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    section_content = self._extract_constraint_section(content, section_keyword)
                    if section_content:
                        combined_constraints.append(f"--- SPECIFIC FILE RULES ({target_path.name}) ---\n{section_content}\n")
                logger.info(f"    > Loaded specific constraints: {specific_file_path}")
            except Exception as e:
                logger.warning(f"Failed to read specific constraints at {specific_file_path}: {e}")
                
        # ---------------------------------------------------------
        # 3. Return Combined Result
        # ---------------------------------------------------------
        if not combined_constraints:
            return ""

        return "\n".join(combined_constraints)

    def run_analysis(
        self,
        output_filename: str = "detailed_code_review.xlsx",
        email_recipients: Optional[List[str]] = None,
        adapter_results: Optional[Dict] = None,
    ) -> str:
        """
        Main Execution Pipeline:
        1. Ingestion (CCLS Indexing) - Only if enabled (Indexes full repo for context)
        2. File Discovery (Handles single file target)
        3. Semantic Analysis (File -> Chunk -> Fetch Context -> LLM)
        4. Reporting (Excel + Email)

        :param output_filename: Name/path for the Excel report.
        :param email_recipients: List of email addresses to send report to.
                                 If None, uses config or skips.
        :param adapter_results: Optional dict of deep static adapter results.
                                If provided, appends static_ tabs to the Excel.
        :return: Path to the generated Excel report.
        """
        logger.info(f"[*] Starting analysis (Run ID: {self.run_id}) on: {self.codebase_path}")
        logger.info(f"[*] Output Directory: {self.output_dir}")
        if self.file_to_fix:
            logger.info(f"[*] Targeted Mode: Analyzing specific file '{self.file_to_fix}'")

        # --- 1. Ingestion Step ---
        if self.use_ccls and self.ingestion:
            logger.info(f"[*] Triggering CCLS Ingestion for '{self.project_name}'...")
            try:
                self.is_indexed = self.ingestion.run_indexing(
                    project_root=str(self.codebase_path),
                    output_dir=self.output_dir,
                    unique_project_prefix=self.project_name,
                    exclude_dirs=list(self.exclude_dirs),
                    exclude_globs=self.exclude_globs,
                )
                if self.is_indexed:
                    logger.info(
                        f"[*] CCLS Ingestion SUCCESS — cache at: "
                        f"{os.path.join(os.path.abspath(self.output_dir), '.ccls-cache')}"
                    )
                else:
                    logger.warning(
                        "[!] Warning: Ingestion failed or timed out. "
                        "Analysis will proceed without semantic context."
                    )
            except Exception as e:
                logger.error(f"[!] Critical Error during CCLS Ingestion: {e}")
                self.is_indexed = False
        elif self.use_ccls and not self.ingestion:
            logger.warning(
                "[!] CCLS requested but ingestion service is None "
                "(dependency_builder not installed?). No CCLS context will be available."
            )

        # --- 2. Gather Files ---
        files = self._gather_files()
        file_count = len(files)

        if file_count == 0:
            logger.warning("[!] No matching files found to analyze. Exiting.")
            return ""

        logger.info(f"[*] Found {file_count} C/C++ files to analyze.")

        # --- 3. Analysis Loop ---
        for i, file_path in enumerate(files):
            try:
                rel_path = str(file_path.relative_to(self.codebase_path))
            except ValueError:
                rel_path = str(file_path)

            logger.info(f"[{i+1}/{file_count}] Analyzing: {rel_path}...")

            try:
                self._analyze_single_file(file_path, rel_path)
            except Exception as e:
                logger.error(f"    ! Error analyzing {rel_path}: {e}")
                self.errors.append({"file": rel_path, "error": str(e)})

        # --- 4. Report Generation ---
        json_path = os.path.join(self.output_dir, "llm_analysis_metrics.jsonl")
        self._generate_json_metrics(json_path)

        # --- PATH FIX: Handle output filename intelligently ---
        if os.path.isabs(output_filename):
            full_out_path = output_filename
        elif os.path.dirname(output_filename):
            full_out_path = output_filename
        else:
            full_out_path = os.path.join(self.output_dir, output_filename)

        excel_path = self._generate_excel_report(full_out_path, adapter_results=adapter_results)

        # --- 5. Email Notification ---
        if email_recipients is None and self.config:
            email_recipients = self.config.get("email.recipients")

        if email_recipients:
            self._trigger_email_report(email_recipients, excel_path, file_count)
        else:
            logger.info("[*] Skipping email report: No recipients configured.")

        # --- 6. CCLS Artifact Cleanup ---
        if self.use_ccls:
            try:
                from dependency_builder.cleanup import cleanup_ccls_artifacts
                logger.info("[*] Cleaning up CCLS artifacts...")
                cleanup_stats = cleanup_ccls_artifacts(
                    output_dir=self.output_dir,
                    project_root=str(self.codebase_path),
                )
                mb = cleanup_stats["bytes_freed"] / (1024 * 1024)
                logger.info(
                    f"[*] CCLS cleanup: {cleanup_stats['files_removed']} files, "
                    f"{cleanup_stats['dirs_removed']} dirs removed ({mb:.1f} MB freed)"
                )
            except Exception as e:
                logger.warning(f"[!] CCLS cleanup failed: {e}")

        return excel_path

    def _analyze_single_file(self, file_path: Path, rel_path: str):
        """
        Analyzes a single file by splitting it into Semantic Chunks.
        Fetches dependency context for each chunk to ensure high accuracy.
        INJECTS: "Issue Identification Rules" from common and specific constraints.
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                code_content = f.read()

            if not code_content.strip():
                return

            # --- Load "Issue Identification Rules" Constraints for this file ---
            constraints_context = self._load_constraints(rel_path, section_keyword="Issue Identification Rules")

            # --- Resolve header includes for context-aware analysis ---
            file_includes = []
            if self.header_context_builder:
                try:
                    file_includes = self.header_context_builder.resolve_includes(str(file_path))
                    if file_includes:
                        resolved = [inc for inc in file_includes if inc.resolved]
                        logger.debug(
                            f"    Header includes for {rel_path}: "
                            f"{len(file_includes)} total, {len(resolved)} resolved"
                        )
                        for inc in resolved[:5]:
                            logger.debug(f"      ✓ {inc.name} → {inc.abs_path}")
                    else:
                        logger.debug(f"    No #include directives found in {rel_path}")
                except Exception as hdr_err:
                    logger.debug(f"    Header resolution failed for {rel_path}: {hdr_err}")
            else:
                logger.debug(f"    HeaderContextBuilder not available (builder=None)")

            # 1. Physical Chunking (Brace Counting)
            chunks = self._smart_chunk_code(code_content)
            total_chunks = len(chunks)
            prev_chunk_tail = ""

            for chunk_idx, (chunk_text, start_line) in enumerate(chunks):

                # Prepare numbered lines for the prompt
                raw_lines = chunk_text.split('\n')
                chunk_line_count = len(raw_lines)
                end_line = start_line + chunk_line_count

                numbered_lines = []
                for idx, line in enumerate(raw_lines):
                    numbered_lines.append(f"{start_line + idx:5d} | {line}")

                numbered_code_block = "\n".join(numbered_lines)

                # 2. Context Retrieval (Dependencies)
                dependency_context = ""
                # Only fetch dependencies if enabled, available, and indexed
                if self.use_ccls and self.is_indexed and self.dep_service:
                    dependency_context = self._fetch_chunk_dependencies(
                        rel_path, start_line, end_line
                    )
                elif self.use_ccls:
                    # Log why CCLS context was skipped despite being requested
                    logger.debug(
                        f"    CCLS skipped for {rel_path}: "
                        f"is_indexed={self.is_indexed}, "
                        f"dep_service={'yes' if self.dep_service else 'None'}"
                    )

                # 2b. Header Context (struct/enum/macro definitions from included headers)
                header_context = ""
                if self.header_context_builder and file_includes:
                    try:
                        header_context = self.header_context_builder.build_context_for_chunk(
                            chunk_text, file_includes
                        )
                        if header_context:
                            logger.debug(
                                f"    Header context for chunk {chunk_idx+1}: "
                                f"{len(header_context)} chars injected"
                            )
                        else:
                            logger.debug(
                                f"    Header context for chunk {chunk_idx+1}: "
                                f"empty (no referenced definitions found in {len(file_includes)} headers)"
                            )
                    except Exception as hctx_err:
                        logger.debug(f"    Header context build failed: {hctx_err}")
                elif self.header_context_builder and not file_includes:
                    logger.debug(f"    Header context skipped: no resolved includes for {rel_path}")

                # 2c. Context Validation (per-chunk false positive reduction)
                validation_context = ""
                if self.context_validator:
                    try:
                        val_report = self.context_validator.analyze_chunk(
                            chunk_text, str(file_path), code_content, start_line
                        )
                        validation_context = val_report.format_summary(max_chars=10000)
                        if validation_context:
                            logger.debug(
                                f"    Validation context for chunk {chunk_idx+1}: "
                                f"{len(validation_context)} chars, "
                                f"{len(val_report.validations)} symbols checked"
                            )
                    except Exception as cv_err:
                        logger.debug(f"    Context validation failed: {cv_err}")

                # 2d. Call Stack Context (cross-function call chain tracing)
                call_stack_context = ""
                if self.call_stack_analyzer:
                    try:
                        call_stack_context = self.call_stack_analyzer.analyze_chunk(
                            chunk_text, str(file_path), code_content, start_line
                        )
                        if call_stack_context:
                            logger.debug(
                                f"    Call stack context for chunk {chunk_idx+1}: "
                                f"{len(call_stack_context)} chars injected"
                            )
                    except Exception as csa_err:
                        logger.debug(f"    Call stack analysis failed: {csa_err}")

                # 3. Context Construction (Previous Chunk Tail + Dependencies + Header Defs)
                context_header = ""
                if prev_chunk_tail:
                    context_header += (
                        f"// ... [CONTEXT: Previous Lines] ...\n"
                        f"{prev_chunk_tail}\n"
                    )

                if dependency_context:
                    context_header += (
                        f"\n// ... [CONTEXT: External Dependencies & Definitions] ...\n"
                        f"{dependency_context}\n"
                        f"// ... [End External Context] ...\n"
                    )

                if header_context:
                    context_header += (
                        f"\n{header_context}\n"
                    )

                if validation_context:
                    context_header += (
                        f"\n{validation_context}\n"
                    )

                if call_stack_context:
                    context_header += (
                        f"\n{call_stack_context}\n"
                    )

                final_chunk_text = (
                    f"{context_header}\n"
                    f"// ... [CURRENT CHUNK START: {rel_path} Lines {start_line}-{end_line}] ...\n"
                    f"{numbered_code_block}"
                )

                # Update overlap for next chunk
                if len(numbered_lines) > self.OVERLAP_LINES:
                    prev_tail_lines = numbered_lines[-self.OVERLAP_LINES:]
                else:
                    prev_tail_lines = numbered_lines
                prev_chunk_tail = "\n".join(prev_tail_lines)

                if total_chunks > 1:
                    logger.info(f"    > Processing Chunk {chunk_idx + 1}/{total_chunks} (Lines {start_line}-{end_line})...")

                # ── HITL: check if this issue should be skipped ─────────
                if self.hitl_context:
                    if self.hitl_context.should_skip_issue("code_quality", rel_path):
                        logger.debug("HITL: skipping %s (marked skip in feedback)", rel_path)
                        continue

                # 4. LLM Call
                # INJECT CONSTRAINTS HERE (Specifically labeled for Identification)
                prompt_constraints_section = ""
                if constraints_context:
                    prompt_constraints_section = f"""
                    ========================================
                    MANDATORY IDENTIFICATION RULES (IGNORE FALSE POSITIVES)
                    ========================================
                    {constraints_context}
                    ========================================
                    """

                final_prompt = f"""
                {CODEBASE_ANALYSIS_PROMPT}

                {prompt_constraints_section}

                TARGET SOURCE CODE ({rel_path} - Part {chunk_idx+1}/{total_chunks}):
                ```cpp
                {final_chunk_text}
                ```
                """

                # ── HITL: augment prompt with feedback context ──────────
                if self.hitl_context:
                    final_prompt = self.hitl_context.augment_prompt(
                        original_prompt=final_prompt,
                        issue_type="code_quality",
                        file_path=rel_path,
                        agent_type="llm_agent",
                    )

                # ── Debug: dump prompt + chunk to {output_dir}/prompt_dumps/ ──
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        dump_dir = os.path.join(self.output_dir, "prompt_dumps")
                        os.makedirs(dump_dir, exist_ok=True)
                        safe_name = rel_path.replace("/", "__").replace("\\", "__")
                        dump_path = os.path.join(
                            dump_dir,
                            f"{safe_name}_chunk{chunk_idx + 1}.txt",
                        )
                        with open(dump_path, "w", encoding="utf-8") as df:
                            df.write(final_prompt)
                        logger.debug(f"    Prompt dump: {dump_path}")
                    except Exception:
                        pass  # never fail on debug dump

                response = self.llm_tools.llm_call(final_prompt)

                # 5. Parsing
                parsed_issues = self._parse_llm_response(
                    response,
                    rel_path,
                    chunk_text=chunk_text,
                    start_line=start_line,
                    chunk_line_count=chunk_line_count
                )

                if parsed_issues:
                    self.results.extend(parsed_issues)

                    # ── HITL: record decisions for future runs ──────────────
                    if self.hitl_context:
                        for result in parsed_issues:
                            self.hitl_context.record_agent_decision(
                                agent_name="CodebaseLLMAgent",
                                issue_type=result.get("Category", "code_quality"),
                                file_path=result.get("File", ""),
                                decision="FIX",
                                code_snippet=result.get("Code", ""),
                                severity=result.get("Severity", "medium"),
                            )

        except Exception as e:
            raise e

    def _fetch_chunk_dependencies(self, rel_path: str, start_line: int, end_line: int) -> str:
        """
        Uses DependencyService to fetch relevant definitions (structs, globals, macros)
        used within the specified line range.
        """
        try:
            logger.debug(
                f"    CCLS fetch: {rel_path} lines {start_line}-{end_line} "
                f"(use_ccls={self.use_ccls}, is_indexed={self.is_indexed}, "
                f"dep_service={'yes' if self.dep_service else 'None'})"
            )

            # Use 'fetch_dependencies_by_file' to get context for the range
            response = self.dep_service.perform_fetch(
                project_root=str(self.codebase_path),
                output_dir=self.output_dir,
                codebase_identifier=self.project_name,
                endpoint_type="fetch_dependencies_by_file",
                file_name=rel_path,
                start=start_line,
                end=end_line,
                level=1
            )

            msg = response.get("message", "")
            data = response.get("data", [])
            logger.debug(
                f"    CCLS response: msg='{msg}', data_items={len(data) if isinstance(data, list) else type(data).__name__}"
            )

            if not data:
                return ""

            # Format the JSON data into a C-comment style block for the LLM
            context_str = []
            if isinstance(data, list):
                for item in data[:10]: # Limit to top 10 to save tokens
                    if isinstance(item, dict):
                        name = item.get("name", "Unknown")
                        # "definition" is the key returned by CCLSDependencyBuilder
                        snippet = (item.get("definition") or item.get("snippet") or "").strip()
                        file_src = item.get("file", "")
                        if snippet:
                            context_str.append(f"// {name} from {file_src}:\n{snippet}")

            result = "\n\n".join(context_str)
            if result:
                logger.debug(f"    CCLS context: {len(context_str)} definitions, {len(result)} chars")
            else:
                logger.debug(f"    CCLS context: empty (data had {len(data)} items but no usable definitions)")
            return result

        except Exception as e:
            logger.warning(f"[!] Warning: Dependency fetch failed for {rel_path}: {e}")
            return ""

    def _smart_chunk_code(self, content: str) -> List[Tuple[str, int]]:
        """
        Splits code using Brace Counting logic.
        Ensures splits happen only at balance 0 (between functions).
        """
        if len(content) < self.TARGET_CHUNK_CHARS * 1.5:
            return [(content, 1)]

        chunks = []
        current_pos = 0
        total_len = len(content)

        last_calc_pos = 0
        last_calc_line = 1

        def get_line_at_index(target_index):
            nonlocal last_calc_pos, last_calc_line
            segment = content[last_calc_pos:target_index]
            newlines = segment.count('\n')
            last_calc_line += newlines
            last_calc_pos = target_index
            return last_calc_line

        while current_pos < total_len:
            start_index = current_pos
            start_line = get_line_at_index(start_index)

            if (total_len - start_index) < self.TARGET_CHUNK_CHARS:
                chunks.append((content[start_index:], start_line))
                break

            scan_idx = start_index
            brace_balance = 0
            found_split = False

            # Scan for a split point
            while scan_idx < total_len:
                char = content[scan_idx]
                if char == '{':
                    brace_balance += 1
                elif char == '}':
                    brace_balance -= 1

                chunk_size = scan_idx - start_index

                # Check for split condition: Enough chars AND balanced braces
                if chunk_size >= self.TARGET_CHUNK_CHARS and brace_balance <= 0:
                    split_idx = scan_idx + 1
                    chunks.append((content[start_index:split_idx], start_line))
                    current_pos = split_idx
                    found_split = True
                    break

                scan_idx += 1

            # Fallback if no clean split found (e.g., massive function)
            if not found_split:
                limit = min(total_len, start_index + self.TARGET_CHUNK_CHARS + 5000)
                newline_search = content.find('\n', limit)
                split_idx = newline_search + 1 if newline_search != -1 else total_len
                chunks.append((content[start_index:split_idx], start_line))
                current_pos = split_idx

        return chunks


    def _gather_files(self) -> List[Path]:
        """Recursively find C/C++ files, OR return the specific file_to_fix if set."""

        # --- Single File Logic ---
        if self.file_to_fix:
            target_path = Path(self.file_to_fix)

            # If path is relative, resolve it against codebase_path
            if not target_path.is_absolute():
                target_path = self.codebase_path / target_path

            if target_path.exists() and target_path.is_file():
                return [target_path]
            else:
                logger.error(f"[!] Error: Target file not found: {target_path}")
                return []

        # --- Bulk Logic ---
        found_files = []
        for root, dirs, filenames in os.walk(self.codebase_path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            for fname in filenames:
                path = Path(root) / fname
                if path.suffix.lower() not in self.C_EXTS:
                    continue
                # Apply glob exclusion patterns (relative to codebase root)
                if self.exclude_globs:
                    try:
                        rel_path_str = path.relative_to(self.codebase_path).as_posix().lower()
                    except ValueError:
                        continue
                    if any(fnmatch.fnmatch(rel_path_str, pat.lower()) for pat in self.exclude_globs):
                        continue
                found_files.append(path)
                if len(found_files) >= self.max_files:
                    return found_files
        return found_files

    def _parse_llm_response(self, response: str, file_path: str, chunk_text: str, start_line: int, chunk_line_count: int) -> List[Dict]:
        """Parses LLM output using Anchor Logic for exact line matching."""
        issues = []
        raw_blocks = response.split("---ISSUE---")

        for block in raw_blocks:
            block = block.strip()
            if not block:
                continue

            issue_data = {"File": file_path}

            patterns = {
                "Title": r"Title:\s*(.+)",
                "Severity": r"Severity:\s*(.+)",
                "Confidence": r"Confidence:\s*(.+)",
                "Category": r"Category:\s*(.+)",
                "Description": r"Description:\s*(.+)",
                "Suggestion": r"Suggestion:\s*(.+)",
            }

            for key, pattern in patterns.items():
                match = re.search(pattern, block, re.IGNORECASE)
                issue_data[key] = match.group(1).strip() if match else "N/A"

            code_match = re.search(r"Code:\s*```(?:\w+)?\n(.*?)\n```", block, re.DOTALL)
            if not code_match:
                code_match = re.search(r"Code:\s*(.+?)(?=\nFixed_Code:|$)", block, re.DOTALL)
            raw_code_snippet = code_match.group(1).strip() if code_match else ""
            issue_data["Code"] = raw_code_snippet

            fixed_match = re.search(r"Fixed_Code:\s*```(?:\w+)?\n(.*?)\n```", block, re.DOTALL)
            if not fixed_match:
                fixed_match = re.search(r"Fixed_Code:\s*(.+?)(?=$)", block, re.DOTALL)
            issue_data["Fixed_Code"] = fixed_match.group(1).strip() if fixed_match else "N/A"

            # --- ANCHOR LOGIC ---
            calculated_line = 0
            found_by_anchor = False

            if raw_code_snippet:
                idx = chunk_text.find(raw_code_snippet)
                if idx == -1:
                    first_line = raw_code_snippet.split('\n')[0].strip()
                    if len(first_line) > 10:
                        idx = chunk_text.find(first_line)

                if idx != -1:
                    newlines_before = chunk_text[:idx].count('\n')
                    calculated_line = start_line + newlines_before
                    issue_data["Line"] = str(calculated_line)
                    found_by_anchor = True

            if not found_by_anchor:
                line_match = re.search(r"Line\D*(\d+)", block, re.IGNORECASE)
                if line_match:
                    raw_val = int(line_match.group(1))
                    if raw_val < chunk_line_count and raw_val < start_line:
                        issue_data["Line"] = str(start_line + raw_val - 1)
                    else:
                        issue_data["Line"] = str(raw_val)
                else:
                    issue_data["Line"] = str(start_line)

            if issue_data["Title"] != "N/A":
                issues.append(issue_data)

        return issues

    def _generate_json_metrics(self, output_path: str):
        """Generate JSONL metrics file for analysis results."""
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            for item in self.results:
                agent_metric = {
                    "run_id": self.run_id,
                    "generated_at": self.start_time.isoformat(),
                    "file_path": item.get("File"),
                    "line_number": int(item.get("Line", 0)),
                    "severity": item.get("Severity"),
                    "issue_type": item.get("Category"),
                    "bad_code_snippet": item.get("Code"),
                    "suggested_fix": item.get("Fixed_Code"),
                    "rationale": item.get("Suggestion")
                }
                f.write(json.dumps(agent_metric) + '\n')

    def _write_vector_ndjson(self, output_path: str) -> str:
        """Write LLM analysis results as vector-DB-ready NDJSON.

        Transforms ``self.results`` into records whose field names align with
        the default ``vector_db_fields`` and ``metadata_fields`` used by
        :class:`db.ndjson_processor.NDJSONProcessor`, so the existing
        ``VectorDbPipeline`` can ingest them without any configuration changes.

        Args:
            output_path: Destination file path for the NDJSON output.

        Returns:
            The *output_path* that was written to.
        """
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        logger = logging.getLogger(__name__)
        written = 0

        with open(output_path, "w", encoding="utf-8") as fh:
            for idx, item in enumerate(self.results):
                file_path = item.get("File", "")
                line_num = int(item.get("Line", 0) or 0)
                # Build a content-based ID so re-runs with different ordering
                # produce the same UUID for the same finding (dedup-safe).
                stable_id = (
                    f"llm_finding::{file_path}::{line_num}"
                    f"::{item.get('Category', '')}::{item.get('Title', '')}"
                )
                record = {
                    # ── Identity / UUID keys ─────────────────────────────
                    "record_type": "llm_review_finding",
                    "id": stable_id,
                    "source": "llm_code_review",

                    # ── Text fields → page_content ───────────────────────
                    "title": item.get("Title", ""),
                    "description": item.get("Description", ""),
                    "recommendation": item.get("Suggestion", ""),
                    "details": item.get("Code", ""),
                    "notes": item.get("Fixed_Code", ""),
                    "summary": item.get("Category", ""),

                    # ── Metadata fields ──────────────────────────────────
                    "severity": item.get("Severity", ""),
                    "category": item.get("Category", ""),
                    "status": item.get("Confidence", ""),
                    "file_relative_path": file_path,
                    "file_name": os.path.basename(file_path) if file_path else "",
                    "module": os.path.dirname(file_path) if file_path else "",
                    "line": line_num,

                    # ── Traceability ─────────────────────────────────────
                    "name": item.get("Title", ""),
                    "violation_type": item.get("Category", ""),
                    "violation_message": item.get("Description", ""),
                    "message": item.get("Suggestion", ""),
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

        logger.info(
            "Wrote %d LLM review records to vector-ready NDJSON: %s",
            written, output_path,
        )
        return output_path

    def _generate_excel_report(
        self, output_path: str, adapter_results: Optional[Dict] = None
    ) -> str:
        """Generates comprehensive Excel file using ExcelWriter.

        Args:
            output_path: Path for the output Excel file.
            adapter_results: Optional dict of adapter_name -> result dict from
                deep static analysis adapters. If provided, each adapter's
                details are written as a static_<name> tab.
        """
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        try:
            writer = ExcelWriter(output_path)

            # --- 1. Summary Sheet ---
            end_time = datetime.now()
            duration_str = str(end_time - self.start_time).split('.')[0]

            summary_metadata = {
                "Report Title": "Codebase Analysis Report",
                "Run ID": self.run_id,
                "Date": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Project Path": str(self.codebase_path),
                "Total Issues": len(self.results),
                "Start Time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "End Time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Duration": duration_str,
            }

            # CHANGE: Passed title as positional argument. Removed 'title' keyword.
            writer.add_data_sheet(
                summary_metadata,
                "Codebase Analysis Report"
            )

            # --- 2. Analysis Sheet with Results ---
            if self.results:
                headers = [
                    "S.No", "Title", "Severity", "Confidence", "Category",
                    "File", "Line", "Description", "Suggestion",
                    "Code", "Fixed_Code", "Feedback", "Constraints"
                ]

                data_rows = []
                for idx, result in enumerate(self.results, start=1):
                    row = [
                        idx,
                        result.get("Title", ""),
                        result.get("Severity", ""),
                        result.get("Confidence", ""),
                        result.get("Category", ""),
                        result.get("File", ""),
                        result.get("Line", ""),
                        result.get("Description", ""),
                        result.get("Suggestion", ""),
                        result.get("Code", ""),
                        result.get("Fixed_Code", ""),
                        result.get("Feedback", ""),
                        result.get("Constraints", ""),
                    ]
                    data_rows.append(row)

                # CHANGE: Passed "Analysis" as positional argument
                writer.add_table_sheet(
                    headers,
                    data_rows,
                    "Analysis",
                    status_column="Severity"
                )
            else:
                logger.info("[*] No critical issues found. Generating empty report.")
                headers = [
                    "S.No", "Title", "Severity", "Confidence", "Category",
                    "File", "Line", "Description", "Suggestion",
                    "Code", "Fixed_Code", "Feedback", "Constraints"
                ]
                writer.add_table_sheet(headers, [], "Analysis")

            # --- 3. Append static_ adapter tabs (if available) ---
            if adapter_results:
                adapter_headers = [
                    "File", "Function", "Line", "Description",
                    "Severity", "Category", "CWE"
                ]
                for adapter_name, result in adapter_results.items():
                    details = result.get("details", [])
                    if not details:
                        continue
                    sheet_name = f"static_{adapter_name}"[:31]
                    rows = [
                        [
                            d.get("file", ""),
                            d.get("function", ""),
                            d.get("line", ""),
                            d.get("description", ""),
                            d.get("severity", ""),
                            d.get("category", ""),
                            d.get("cwe", ""),
                        ]
                        for d in details
                    ]
                    writer.add_table_sheet(
                        adapter_headers, rows,
                        sheet_name,
                        status_column="Severity",
                    )

            writer.save()
            logger.info(f"[*] Success! Report saved to: {output_path}")

        except Exception as e:
            logger.error(f"Error creating Excel: {e}")
            # Fallback: try CSV
            try:
                csv_path = output_path.replace(".xlsx", ".csv")
                import csv as csvmodule
                with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                    if self.results:
                        fieldnames = list(self.results[0].keys())
                        writer_csv = csvmodule.DictWriter(f, fieldnames=fieldnames)
                        writer_csv.writeheader()
                        writer_csv.writerows(self.results)
                logger.warning(f"Fell back to CSV: {csv_path}")
                return csv_path
            except Exception as csv_err:
                logger.error(f"CSV fallback also failed: {csv_err}")
                raise

        return output_path

    def _trigger_email_report(self, recipients: List[str], attachment_path: str, file_count: int):
        """
        Sends a comprehensive HTML email report with detailed stats and actionable next steps.
        Uses EmailReporter for sending.
        """
        try:
            # --- 1. Statistics Calculation ---
            total_issues = len(self.results)
            crit_count = sum(1 for i in self.results if "critical" in str(i.get("Severity", "")).lower())
            high_count = sum(1 for i in self.results if "high" in str(i.get("Severity", "")).lower())

            # Map stats to the visual dashboard cards
            stats = {
                "Files Scanned": file_count,
                "Total Issues Detected": total_issues,
                "Critical Severity": crit_count,
                "High Severity": high_count
            }

            # --- 2. Enhanced Metadata (The "Run Details" Table) ---
            end_time = datetime.now()
            duration_str = str(end_time - self.start_time).split('.')[0]

            metadata = {
                "Agent Type": "CodebaseLLMAgent (Analysis Only)",
                "Execution Mode": "LIVE (Read-Only Scan)",
                "Project Root": self.codebase_path.name,
                "Full Path": str(self.codebase_path),
                "Start Time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "End Time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Duration": duration_str,
                "Report File": os.path.basename(attachment_path)
            }

            # --- 3. Comprehensive Summary / Post-Processing ---
            if total_issues == 0:
                summary_intro = "Clean Scan: No issues detected based on current heuristics."
            else:
                summary_intro = (
                    f"The agent successfully scanned {file_count} files and identified {total_issues} potential issues. "
                    f"Top concerns include {crit_count} Critical and {high_count} High severity items."
                )

            analysis_summary = (
                f"Execution Summary:\n"
                f"{summary_intro}\n\n"

                f"Post-Processing Action Items:\n"
                f"1. Triage: Open the attached {os.path.basename(attachment_path)}. Use Excel filters to isolate 'Critical' and 'High' severity rows.\n"
                f"2. Validation: Verify the 'Code' and 'Suggestion' columns to confirm validity (check for false positives).\n"
                f"3. Assignment: Create tickets for valid Critical issues. The 'Fixed_Code' column can be used as a starting point for developers.\n"
                f"4. Remediation: For automated fixing, feed the verified rows into the CodebaseFixerAgent pipeline."
            )

            # --- 4. Send Email ---
            reporter = EmailReporter()
            # CHANGE: Removed 'title' and 'subject' arguments entirely.
            success = reporter.send_report(
                recipients=recipients,
                metadata=metadata,
                stats=stats,
                analysis_summary=analysis_summary,
                attachment_path=attachment_path
            )

            if success:
                logger.info(" ")
            else:
                logger.warning("")

        except Exception as e:
            logger.error(f"[!] Error during report generation: {e}")

    def get_results(self) -> Dict:
        """
        Returns structured results for pipeline orchestration.
        Useful for multi-agent workflows.
        """
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        return {
            "run_id": self.run_id,
            "status": "completed" if self.results else "no_issues",
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": duration,
            "project": str(self.codebase_path),
            "total_issues": len(self.results),
            "total_errors": len(self.errors),
            "critical_count": sum(1 for r in self.results if "critical" in str(r.get("Severity", "")).lower()),
            "high_count": sum(1 for r in self.results if "high" in str(r.get("Severity", "")).lower()),
            "results": self.results,
            "errors": self.errors,
        }