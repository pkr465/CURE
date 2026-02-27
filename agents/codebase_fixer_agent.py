import os
import sys
import json
import shutil
import logging
import re
import time
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime

# -------------------------------------------------------------------------
# DEPENDENCY SERVICE INTEGRATION
# -------------------------------------------------------------------------
try:
    from dependency_builder.dependency_service import DependencyService
    from dependency_builder.config import DependencyBuilderConfig
    DEPENDENCY_SERVICE_AVAILABLE = True
except ImportError:
    DEPENDENCY_SERVICE_AVAILABLE = False
    DependencyService = None
    DependencyBuilderConfig = None

# -------------------------------------------------------------------------
# LLM TOOLS INTEGRATION
# -------------------------------------------------------------------------
try:
    from utils.common.llm_tools import LLMTools, LLMConfig
except ImportError:
    raise ImportError("LLMTools not found. Ensure utils.common.llm_tools is available.")

# -------------------------------------------------------------------------
# EMAIL REPORTER INTEGRATION
# -------------------------------------------------------------------------
try:
    from utils.common.email_reporter import EmailReporter
except ImportError:
    EmailReporter = None

# -------------------------------------------------------------------------
# EXCEL WRITER INTEGRATION
# -------------------------------------------------------------------------
try:
    from utils.common.excel_writer import ExcelWriter, ExcelStyle
except ImportError:
    raise ImportError("ExcelWriter not found. Ensure utils.common.excel_writer is available.")

# -------------------------------------------------------------------------
# GLOBAL CONFIG INTEGRATION
# -------------------------------------------------------------------------
try:
    from utils.parsers.global_config_parser import GlobalConfig
except ImportError:
    GlobalConfig = None

# -------------------------------------------------------------------------
# HITL SUPPORT (OPTIONAL)
# -------------------------------------------------------------------------
try:
    from hitl import HITLContext, HITL_AVAILABLE
except ImportError:
    HITLContext = None
    HITL_AVAILABLE = False

# Function parameter validator (per-chunk parameter validation context)
try:
    from agents.context.function_param_validator import FunctionParamValidator
    PARAM_VALIDATOR_AVAILABLE = True
except ImportError:
    FunctionParamValidator = None
    PARAM_VALIDATOR_AVAILABLE = False


class CodebaseFixerAgent:
    """
    Holistic Fixer Agent with Semantic Context Awareness.

    UPDATES:
    1. Robust Auth Check: Catches 'Missing credentials' and '401' errors at startup.
    2. Defensive Dependency Fetching: Handles malformed/None returns safely.
    3. Smart Chunking: Tokenizer-based splitting (Strings/Comments aware).
    4. Atomic Writes: Prevents file corruption.
    5. Constraint Injection: Loads 'Issue Resolution Rules' from constraint files to guide the fix.
    """

    TARGET_CHUNK_CHARS = 8000
    HARD_CHUNK_LIMIT = 20000
    CONTEXT_OVERLAP_LINES = 25

    def __init__(
        self,
        codebase_root: str,
        directives_file: str,
        backup_dir: str,
        output_dir: str = "./out",
        config: Optional['GlobalConfig'] = None,
        llm_tools: Optional[LLMTools] = None,
        dep_config: Optional['DependencyBuilderConfig'] = None,
        dry_run: bool = False,
        verbose: bool = False,
        hitl_context: Optional['HITLContext'] = None,
        constraints_dir: str = "agents/constraints",
        telemetry=None,
        telemetry_run_id: Optional[str] = None,
    ):
        self.codebase_root = Path(codebase_root).resolve()
        self.directives_path = Path(directives_file).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.output_dir = str(Path(output_dir).resolve())
        self.project_name = self.codebase_root.name
        self.start_time = datetime.now()

        # Configuration
        self.config = config
        self.dry_run = dry_run
        self.verbose = verbose
        self.hitl_context = hitl_context

        # Telemetry (optional)
        self._telemetry = telemetry
        self._telemetry_run_id = telemetry_run_id

        # Audit trail for detailed tracking of every decision
        self.audit_trail: List[Dict] = []

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

        # Setup Logging — use module logger only (do NOT call basicConfig which
        # installs a StreamHandler on the root logger and floods the UI console)
        self.logger = logging.getLogger(__name__)

        # Initialize LLM Tools
        self._initialize_llm_tools(llm_tools)

        # Initialize Dependency Service
        self._initialize_dependency_service(dep_config)

        # Function Parameter Validator
        self.param_validator = None
        if PARAM_VALIDATOR_AVAILABLE:
            try:
                self.param_validator = FunctionParamValidator(
                    codebase_path=str(self.codebase_root),
                )
                self.logger.info("[*] FunctionParamValidator enabled for fixer agent")
            except Exception as fpv_err:
                self.logger.debug(f"FunctionParamValidator init failed: {fpv_err}")
        
        
    def _initialize_llm_tools(self, llm_tools: Optional[LLMTools] = None):
        """Initialize LLM tools with dependency injection pattern."""
        self.llm_tools = LLMTools(model=self.config.get("llm.coding_model"))
        if self.verbose:
            self.logger.info("[Agent] LLM Initialized with defaults from environment.")

    def _initialize_dependency_service(self, dep_config: Optional['DependencyBuilderConfig'] = None):
        """Initialize dependency service with proper configuration."""
        if not DEPENDENCY_SERVICE_AVAILABLE:
            self.logger.warning("[!] DependencyService not available. Fixes will lack semantic context.")
            self.dep_service = None
            return

        try:
            if dep_config:
                self.dep_service = DependencyService(config=dep_config)
            else:
                dep_config = DependencyBuilderConfig.from_env() if hasattr(DependencyBuilderConfig, 'from_env') else DependencyBuilderConfig()
                self.dep_service = DependencyService(config=dep_config)
            if self.verbose:
                self.logger.info("[Agent] DependencyService initialized.")
        except Exception as e:
            self.logger.warning(f"[!] Failed to initialize DependencyService: {e}")
            self.dep_service = None

    def _extract_constraint_section(self, content: str, keyword: str) -> str:
        """
        Parses Markdown content to find a header containing the keyword (e.g., 'Issue Resolution Rules')
        and extracts the text until the next header.
        """
        try:
            # Regex: Find '## ... keyword ...' then capture content until next '## ' or End of String
            pattern = re.compile(
                r"^## .*?" + re.escape(keyword) + r".*?$\n(.*?)(?=^## |\Z)", 
                re.MULTILINE | re.DOTALL | re.IGNORECASE
            )
            match = pattern.search(content)
            if match:
                return match.group(1).strip()
            return ""
        except Exception as e:
            self.logger.warning(f"Failed to extract section '{keyword}': {e}")
            return ""

    def _load_constraints(self, file_name: str, section_keyword: str = "Issue Resolution Rules") -> str:
        """
        Loads constraints from agents/constraints/common_constraints.md
        and agents/constraints/<stem>_constraints.md (recursive search).

        Specifically extracts the section matching 'section_keyword' (default: 'Issue Resolution Rules')
        to ensure the Fixer Agent only receives rules about HOW to fix (not just identification).

        :param file_name: The name of the file being processed (e.g., dp_main.c).
        :param section_keyword: The header keyword to look for in the markdown files.
        :return: A combined string of constraints to inject into the prompt.
        """
        combined_constraints = []
        base_dir = Path(self.constraints_dir)

        # 1. Load Common Constraints
        common_file = base_dir / "common_constraints.md"
        if common_file.exists():
            try:
                with open(common_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    section_content = self._extract_constraint_section(content, section_keyword)
                    if section_content:
                        combined_constraints.append(f"--- GLOBAL RESOLUTION RULES ---\n{section_content}\n")
            except Exception as e:
                self.logger.warning(f"Failed to read common constraints: {e}")

        # 2. Load File-Specific Constraints (Recursive Search)
        # Strip extension: dp_main.c → dp_main, then search for dp_main_constraints.md
        target_stem = Path(file_name).stem
        search_filename = f"{target_stem}_constraints.md"

        specific_file_path = None
        try:
            found_files = list(base_dir.rglob(search_filename))
            if len(found_files) == 1:
                specific_file_path = found_files[0]
            elif len(found_files) > 1:
                specific_file_path = found_files[0]
                self.logger.warning(f"Multiple constraint files found for {search_filename}. Using: {specific_file_path}")
        except Exception as e:
            self.logger.error(f"Error while searching for specific constraints: {e}")

        if specific_file_path and specific_file_path.exists():
            try:
                with open(specific_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    section_content = self._extract_constraint_section(content, section_keyword)
                    if section_content:
                        combined_constraints.append(f"--- SPECIFIC FILE RESOLUTION RULES ({file_name}) ---\n{section_content}\n")
                self.logger.info(f"    > Loaded specific resolution rules: {specific_file_path}")
            except Exception as e:
                self.logger.warning(f"Failed to read specific constraints for {file_name}: {e}")

        if not combined_constraints:
            return ""

        return "\n".join(combined_constraints)

    def run_agent(
        self,
        report_filename: str = "final_execution_audit.xlsx",
        email_recipients: Optional[List[str]] = None
    ) -> Dict:
        """
        Execute the fixer agent on the codebase.

        Args:
            report_filename: Output Excel report filename
            email_recipients: Email addresses for report delivery. If None, uses config.

        Returns:
            Dictionary with execution results
        """
        self.logger.info(f"[*] Starting Holistic Fixer Agent on {self.codebase_root}")
        if self.dep_service:
            self.logger.info(f"[*] Dependency Service Enabled. Using cache at: {self.output_dir}/{self.project_name}")

        if self.dry_run:
            self.logger.info("[NOTICE] DRY RUN MODE ENABLED: No files will be modified.")

        # Resolve email recipients
        if email_recipients is None and self.config:
            email_recipients = self.config.get("email.recipients", [])
        if not email_recipients:
            email_recipients = []

        directives = self._load_directives()
        if not directives:
            self.logger.warning("[!] No directives found.")
            return {"status": "no_directives", "results": []}

        grouped_tasks = self._group_by_file(directives)
        results = []
        file_count = 0
        total_files = len(grouped_tasks)

        for file_path_str, tasks in grouped_tasks.items():
            file_count += 1
            # Path resolution
            if os.path.isabs(file_path_str):
                try:
                    file_path = Path(file_path_str).resolve()
                except ValueError:
                    file_path = Path(file_path_str)
            else:
                file_path = (self.codebase_root / file_path_str).resolve()

            # Use Absolute Path for dependency lookup
            abs_path_str = str(file_path)

            self.logger.info(f"[{file_count}/{total_files}] Processing File: {file_path.name} ({len(tasks)} items)...")

            if not file_path.exists():
                self.logger.warning(f"    [!] File not found: {file_path}")
                for t in tasks:
                    results.append({**t, "final_status": "FILE_NOT_FOUND"})
                    self._audit_decision(t, "FILE_NOT_FOUND", f"File not found: {file_path}")
                continue

            active_tasks = [t for t in tasks if t.get('action') != 'SKIP']
            skipped_tasks = [t for t in tasks if t.get('action') == 'SKIP']

            for t in skipped_tasks:
                results.append({**t, "final_status": "SKIPPED"})
                self._audit_decision(t, "SKIPPED", "Directive action is SKIP")

            if not active_tasks:
                self.logger.info("    -> No active tasks for this file.")
                continue

            try:
                self._backup_file(file_path)
                # errors='replace' to prevent encoding crashes
                original_content = file_path.read_text(encoding='utf-8', errors='replace')

                new_content, chunk_results = self._process_file_in_chunks(
                    file_path.name,
                    abs_path_str,
                    original_content,
                    active_tasks
                )

                if not self._validate_integrity(original_content, new_content):
                    raise ValueError("Safety Guard: New content too short (<80%). Reverting.")

                if not self.dry_run:
                    # Atomic Write
                    self._atomic_write(file_path, new_content)
                    self.logger.info(f"    -> [Success] File rewritten ({len(active_tasks)} tasks processed).")
                else:
                    self.logger.info(f"    -> [Dry Run] File would be rewritten ({len(active_tasks)} tasks processed).")

                results.extend(chunk_results)

            except Exception as e:
                self.logger.error(f"Failed to refactor {file_path.name}: {e}")
                for t in active_tasks:
                    results.append({**t, "final_status": "LLM_FAIL", "details": str(e)})
                    self._audit_decision(t, "LLM_FAIL", str(e))

        report_path = self._save_report(results, report_filename)

        if EmailReporter and not self.dry_run and email_recipients:
            self._trigger_email_report(email_recipients, report_path, results, file_count)
        elif self.dry_run:
            self.logger.info("[!] Dry Run: Email report skipped.")
        elif not email_recipients:
            self.logger.info("[!] No email recipients configured. Report saved to: " + report_path)

        return {
            "status": "completed",
            "report_path": report_path,
            "results": results,
            "files_processed": file_count
        }

    def _process_file_in_chunks(self, filename: str, file_path_abs: str, content: str, all_tasks: List[Dict]) -> Tuple[str, List[Dict]]:
        """Process file in intelligent chunks, applying fixes to each."""
        chunks = self._smart_chunk_code(content)
        final_pieces = []
        processed_results = []
        prev_chunk_tail = ""

        # Load "Issue Resolution Rules" constraints for this specific file
        file_constraints = self._load_constraints(filename, section_keyword="Issue Resolution Rules")

        # Stats
        issues_resolved_so_far = 0
        total_chunks = len(chunks)

        # Detect Language
        language = self._detect_language(Path(filename))

        for i, (chunk_text, start_line) in enumerate(chunks):
            chunk_line_count = chunk_text.count('\n')
            end_line = start_line + chunk_line_count

            chunk_tasks = []
            for task in all_tasks:
                try:
                    task_line = int(task.get('line_number', 0))
                    # Allow fuzzy matching (+/- 5 lines)
                    if (start_line - 5) <= task_line <= (end_line + 5):
                        source_type = task.get("source_type", "unknown")

                        # ── HITL: check if this issue should be skipped ─────────
                        if self.hitl_context:
                            issue_type = task.get("issue_type", "")
                            file_path = task.get("file_path", "")
                            if self.hitl_context.should_skip_issue(issue_type, file_path):
                                self.logger.info(
                                    "HITL: skipping %s in %s (source=%s, marked skip in feedback)",
                                    issue_type, file_path, source_type,
                                )
                                task["action"] = "SKIP"
                                self._audit_decision(
                                    task, "SKIPPED_HITL",
                                    f"HITL feedback says skip {issue_type}",
                                )
                                continue
                        chunk_tasks.append(task)
                except ValueError:
                    continue

            if not chunk_tasks:
                final_pieces.append(chunk_text)
                prev_chunk_tail = self._get_tail_context(chunk_text)
                continue

            self.logger.info(f"    [Running] Chunk {i+1}/{total_chunks}: Fixing lines {start_line}-{end_line} ({len(chunk_tasks)} issues)...")

            start_chunk_time = time.time()
            try:
                if self.dry_run:
                    final_pieces.append(chunk_text)
                    for t in chunk_tasks:
                        processed_results.append({**t, "final_status": "FIXED_SIMULATED", "details": "Dry Run"})
                    self.logger.info(f"Done (Dry Run).")
                    continue

                # --- DEPENDENCY FETCH ---
                dependency_context = ""
                if self.dep_service:
                    dependency_context = self._fetch_dependencies(file_path_abs, start_line, end_line)

                # Pass language and CONSTRAINTS to prompt
                prompt = self._construct_refactor_prompt(
                    filename, 
                    chunk_text, 
                    chunk_tasks, 
                    prev_chunk_tail, 
                    dependency_context, 
                    language,
                    constraints_context=file_constraints  # Inject Resolution Rules
                )

                coding_model = self.config.get("llm.coding_model") if self.config else None
                _llm_t0 = time.time()
                llm_response = self.llm_tools.llm_call(prompt, model=coding_model)
                _llm_ms = int((time.time() - _llm_t0) * 1000)

                # Telemetry: per-call LLM logging
                if self._telemetry and self._telemetry_run_id:
                    try:
                        _usage = getattr(llm_response, "usage", None) or {}
                        if isinstance(_usage, dict):
                            _pt = _usage.get("input_tokens") or _usage.get("prompt_tokens") or 0
                            _ct = _usage.get("output_tokens") or _usage.get("completion_tokens") or 0
                        else:
                            _pt = getattr(_usage, "input_tokens", 0) or 0
                            _ct = getattr(_usage, "output_tokens", 0) or 0
                        _provider = getattr(self.llm_tools, "provider", "")
                        _model = getattr(self.llm_tools, "model", "")
                        self._telemetry.log_llm_call_detailed(
                            run_id=self._telemetry_run_id,
                            provider=_provider,
                            model=_model,
                            purpose="fix",
                            file_path=filename,
                            chunk_index=i,
                            prompt_tokens=int(_pt),
                            completion_tokens=int(_ct),
                            latency_ms=_llm_ms,
                        )
                    except Exception:
                        pass  # never fail on telemetry

                fixed_chunk = self._extract_code_from_response(llm_response)

                duration = round(time.time() - start_chunk_time, 1)

                if fixed_chunk:
                    # ── Structural validation (compilation safety net) ──
                    struct_valid, struct_msg = self._validate_code_structure(chunk_text, fixed_chunk)
                    if not struct_valid:
                        self.logger.warning(
                            f"Structure validation FAILED — reverting chunk {i+1}: {struct_msg}"
                        )
                        final_pieces.append(chunk_text)
                        prev_chunk_tail = self._get_tail_context(chunk_text)
                        for t in chunk_tasks:
                            processed_results.append({
                                **t,
                                "final_status": "STRUCTURE_FAIL",
                                "details": f"Structure validation failed: {struct_msg}",
                            })
                            self._audit_decision(
                                t, "STRUCTURE_FAIL",
                                f"Chunk {i+1}: {struct_msg}",
                            )
                        continue
                    if struct_msg:
                        self.logger.warning(f"Structure warnings (chunk {i+1}): {struct_msg}")

                    # ── Diff validation (advisory — never rejects) ──
                    diff_msg = self._validate_fix_diff(chunk_text, fixed_chunk, filename)
                    if diff_msg:
                        self.logger.warning(f"Diff warnings (chunk {i+1}): {diff_msg}")

                    # ── Truncation check ──
                    if len(fixed_chunk) < len(chunk_text) * 0.4:
                        self.logger.warning(f"Failed (Truncated Response).")
                        final_pieces.append(chunk_text)
                        for t in chunk_tasks:
                            processed_results.append({**t, "final_status": "LLM_FAIL", "details": "Response truncated"})
                    else:
                        final_pieces.append(fixed_chunk)
                        issues_resolved_so_far += len(chunk_tasks)
                        self.logger.info(f"Done in {duration}s.")

                        for t in chunk_tasks:
                            details_str = f"Fixed in Chunk {i+1}"
                            if struct_msg:
                                details_str += f" [WARN: {struct_msg}]"
                            if diff_msg:
                                details_str += f" [WARN: {diff_msg}]"
                            processed_results.append({**t, "final_status": "FIXED", "details": details_str})
                            self._audit_decision(
                                t, "FIXED",
                                f"Fixed in chunk {i+1} ({duration}s)",
                            )
                            # ── HITL: record the decision ──────────────────────
                            if self.hitl_context:
                                self.hitl_context.record_agent_decision(
                                    agent_name="CodebaseFixerAgent",
                                    issue_type=t.get("issue_type", ""),
                                    file_path=t.get("file_path", ""),
                                    decision="FIX",
                                    code_snippet=t.get("bad_code_snippet", ""),
                                )
                        prev_chunk_tail = self._get_tail_context(fixed_chunk)
                else:
                    self.logger.warning(f"Failed (Empty Response).")
                    final_pieces.append(chunk_text)
                    prev_chunk_tail = self._get_tail_context(chunk_text)
                    for t in chunk_tasks:
                        processed_results.append({**t, "final_status": "LLM_FAIL", "details": "Invalid/Empty response"})

            except Exception as e:
                # [FIX] Fail Fast on Auth inside processing loop
                if any(x in str(e).lower() for x in ["missing credentials", "401", "unauthorized"]):
                     self.logger.critical("CRITICAL: LLM Credentials missing/invalid. Aborting.")
                     sys.exit(1)
                     
                self.logger.error(f"Chunk processing error: {e}")
                final_pieces.append(chunk_text)
                prev_chunk_tail = self._get_tail_context(chunk_text)
                for t in chunk_tasks:
                    processed_results.append({**t, "final_status": "LLM_FAIL", "details": str(e)})

        return "".join(final_pieces), processed_results

    def _fetch_dependencies(self, file_path_abs: str, start_line: int, end_line: int) -> str:
        """
        Fetches semantic definitions (structs, globals) to guide the LLM fix.
        [FIX] Hardened against malformed/None responses from DependencyService.
        """
        if not self.dep_service: return ""
        try:
            response = self.dep_service.perform_fetch(
                project_root=str(self.codebase_root),
                output_dir=self.output_dir,
                codebase_identifier=self.project_name,
                endpoint_type="fetch_dependencies_by_file",
                file_name=file_path_abs,
                start=start_line,
                end=end_line,
                level=1
            )
            
            # Safe parsing
            if not response or not isinstance(response, dict):
                return ""
            
            data = response.get("data", [])
            if not data or not isinstance(data, list):
                return ""

            context_str = []
            for item in data[:10]:
                if not item: continue
                
                # Handle both Dict and Object responses safely
                if isinstance(item, dict):
                    name = item.get("name", "Unknown")
                    kind = item.get("kind", "Unknown")
                    snippet = item.get("snippet", "").strip()
                else:
                    # Fallback for objects
                    name = getattr(item, "name", "Unknown")
                    kind = getattr(item, "kind", "Unknown")
                    snippet = getattr(item, "snippet", "").strip()

                if snippet:
                    context_str.append(f"// ({kind}) {name}:\n{snippet}")

            return "\n\n".join(context_str)
        except Exception as e:
            self.logger.warning(f"Dependency fetch skipped due to error: {e}")
            return ""

    def _smart_chunk_code(self, source_code: str) -> List[Tuple[str, int]]:
        """
        [FIX] Robust state-machine tokenizer.
        Splits code into chunks respecting block boundaries (braces).
        Handles nested braces, strings, and C++/Python style comments to avoid false matches.
        """
        if len(source_code) <= self.TARGET_CHUNK_CHARS:
             return [(source_code, 1)]

        chunks = []
        current_chunk = []
        current_len = 0
        current_start_line = 1
        
        lines = source_code.splitlines(keepends=True)
        
        # State machine variables
        depth = 0
        in_string = False
        in_char = False
        in_line_comment = False
        in_block_comment = False
        
        chunk_start_line = 1

        for line in lines:
            current_chunk.append(line)
            current_len += len(line)
            
            i = 0
            while i < len(line):
                char = line[i]
                
                # Handle comments and strings to avoid false brace counting
                if not in_string and not in_char and not in_block_comment and not in_line_comment:
                    if char == '/' and i + 1 < len(line) and line[i+1] == '/':
                        in_line_comment = True
                        i += 1
                    elif char == '/' and i + 1 < len(line) and line[i+1] == '*':
                        in_block_comment = True
                        i += 1
                    elif char == '"': in_string = True
                    elif char == "'": in_char = True
                    elif char == '{': depth += 1
                    elif char == '}': depth = max(0, depth - 1)
                elif in_line_comment and char == '\n': in_line_comment = False
                elif in_block_comment and char == '*' and i + 1 < len(line) and line[i+1] == '/':
                    in_block_comment = False
                    i += 1
                elif in_string and char == '"' and line[i-1] != '\\': in_string = False
                elif in_char and char == "'" and line[i-1] != '\\': in_char = False
                
                i += 1

            # Check split condition
            if current_len >= self.TARGET_CHUNK_CHARS and depth == 0:
                chunk_str = "".join(current_chunk)
                chunks.append((chunk_str, chunk_start_line))
                
                # Update trackers
                chunk_start_line += chunk_str.count('\n')
                current_chunk = []
                current_len = 0
        
        # Append remaining
        if current_chunk:
            chunks.append(("".join(current_chunk), chunk_start_line))
            
        return chunks

    def _detect_language(self, file_path: Path) -> str:
        """Determine coding language for better prompting."""
        ext = file_path.suffix.lower()
        mapping = {
            '.py': 'Python', '.cpp': 'C++', '.cc': 'C++', '.c': 'C', '.h': 'C++', '.hpp': 'C++',
            '.js': 'JavaScript', '.ts': 'TypeScript', '.java': 'Java', '.json': 'JSON', 
            '.html': 'HTML', '.css': 'CSS', '.go': 'Go', '.rs': 'Rust'
        }
        return mapping.get(ext, 'Code')

    def _get_tail_context(self, text: str) -> str:
        """Extract tail context from text for inter-chunk continuity."""
        lines = text.splitlines()
        if len(lines) > self.CONTEXT_OVERLAP_LINES:
            return "\n".join(lines[-self.CONTEXT_OVERLAP_LINES:])
        return text

    def _construct_code_integrity_rules(self) -> str:
        """
        Generate explicit code integrity rules for the LLM prompt.
        These rules address critical compilation-breaking bugs observed in
        previous LLM-generated fixes: newline removal, argument mismatches,
        type declaration removal, incorrect macro usage, and assert errors.
        """
        return """
    ========== CRITICAL CODE INTEGRITY RULES (COMPILATION SAFETY) ==========
    Violating ANY of these rules will cause compilation failures. Follow ALL rules strictly.

    RULE 1 — PRESERVE BLANK LINES AND FORMATTING:
    - DO NOT remove blank lines between functions, after closing braces '}', or between code blocks.
    - If the original code has a blank line after '}', the fixed code MUST also have that blank line.
    - Blank lines are structural separators — removing them merges lines and causes syntax errors.
    - BAD:  '}\\nvoid foo() {'   (merged — will NOT compile)
    - GOOD: '}\\n\\nvoid foo() {' (preserved — compiles correctly)

    RULE 2 — FUNCTION SIGNATURE CONSISTENCY:
    - If you modify a function CALL by adding or removing arguments, you MUST also update the
      function DEFINITION/DECLARATION in the same chunk to match.
    - Count the arguments BEFORE and AFTER your fix — they MUST be identical between calls and definitions.
    - DO NOT add arguments to function calls without updating the corresponding function definition.
    - If the function definition is outside this chunk, do NOT add extra arguments to the call.

    RULE 3 — PRESERVE VARIABLE TYPE DECLARATIONS:
    - In for-loop initializers like 'for (int i = 0; i < N; i++)', the type 'int' is MANDATORY for compilation.
    - DO NOT remove the type declaration from for-loop variables.
    - DO NOT transform 'for (int i = 1; i < X; i++)' into 'for (i = (A_UINT32)1; i < X; i++)'.
    - The type declaration in the for-loop initializer is a variable definition — removing it causes
      'undeclared identifier' compilation errors unless the variable is declared elsewhere.

    RULE 4 — MACRO AVAILABILITY:
    - DO NOT use macros like A_ARRAY_SIZE(arr), ARRAY_SIZE(arr), or similar unless you can see them
      defined in the code chunk, included headers, or the EXTERNAL DEFINITIONS context provided.
    - For array sizing, always prefer the standard C/C++ idiom: sizeof(arr) / sizeof(arr[0])
    - Application-specific macros (A_*, QDF_*, OL_*, etc.) may not be visible to the compiler
      in this translation unit. Use only standard C/C++ constructs when in doubt.

    RULE 5 — ASSERT AND MACRO ARGUMENT COUNTS:
    - A_COMPILE_TIME_ASSERT requires exactly 2 arguments: A_COMPILE_TIME_ASSERT(condition, message_string)
    - WRONG:   A_COMPILE_TIME_ASSERT(sizeof(x) == 4)          — missing second argument
    - CORRECT: A_COMPILE_TIME_ASSERT(sizeof(x) == 4, "size check failed")
    - When in doubt, use standard C++11 static_assert(condition, "message") instead of custom macros.
    - Before using ANY macro, verify its expected argument count from the visible definitions.

    RULE 6 — DO NOT ADD UNNECESSARY TYPE CASTS:
    - DO NOT add casts like (A_UINT32), (uint32_t), (int) to numeric literals unless there is a
      specific type mismatch warning being fixed.
    - Unnecessary casts clutter the code and may hide real issues.
    - Integer literals in C/C++ have well-defined promotion rules — trust the compiler.

    RULE 7 — PRESERVE ORIGINAL INDENTATION AND STYLE:
    - Match the indentation style (tabs vs spaces, indent width) of the original code exactly.
    - DO NOT reformat code that is unrelated to the fix.
    - If the original uses tabs, use tabs. If it uses 4-space indent, use 4-space indent.
    ============================================================================
    """

    def _construct_refactor_prompt(self, filename: str, content: str, issues: List[Dict],
                                 preceding_context: str, dependency_context: str,
                                 language: str, constraints_context: str = "") -> str:
        """Construct a detailed refactoring prompt for the LLM.

        Includes source-type-specific guidance, human feedback, and
        HITL constraint injection for maximum fix quality.
        """
        issues_text = ""
        for i, issue in enumerate(issues, 1):
            source_type = issue.get("source_type", "unknown")
            source_label = {
                "llm": "LLM Code Review",
                "static": "Static Analysis Tool",
                "patch": "Patch Analysis",
            }.get(source_type, "Unknown Source")

            human_feedback = issue.get("human_feedback", "")
            human_constraints = issue.get("human_constraints", "")

            issues_text += (
                f"--- ISSUE #{i} (Source: {source_label}) ---\n"
                f"Location: Line {issue.get('line_number')}\n"
                f"Severity: {issue.get('severity', 'medium')}\n"
                f"Category: {issue.get('issue_type', '')}\n"
                f"Problem: {issue.get('rationale') or issue.get('description') or issue.get('bad_code_snippet', '')}\n"
                f"Suggested Fix: {issue.get('suggested_fix')}\n"
            )
            if human_feedback:
                issues_text += f"Human Reviewer Feedback: {human_feedback}\n"
            if human_constraints:
                issues_text += f"Human Constraints: {human_constraints}\n"
            issues_text += "\n"

        context_section = ""
        if preceding_context:
            context_section += (
                f"--- PREVIOUS CHUNK CONTEXT ---\n"
                f"// ... end of previous lines\n"
                f"{preceding_context}\n"
                f"// ... current chunk follows\n\n"
            )

        if dependency_context:
            context_section += (
                f"--- EXTERNAL DEFINITIONS (SEMANTIC CONTEXT) ---\n"
                f"// Use these definitions (structs, macros, globals) to ensure your fix is valid.\n"
                f"{dependency_context}\n\n"
            )

        # Function parameter validation context
        if self.param_validator:
            try:
                pv_reports = self.param_validator.analyze_chunk(
                    content, filename, content, 1
                )
                pv_context = self.param_validator.format_reports(pv_reports, max_chars=2000)
                if pv_context:
                    context_section += f"\n{pv_context}\n\n"
            except Exception:
                pass

        # ── HITL: inject constraints into fix prompt ────────────
        hitl_constraints_section = ""
        if self.hitl_context:
            hitl_ctx = self.hitl_context.get_augmented_context(
                issue_type=issues[0].get("issue_type", "") if issues else "",
                file_path=issues[0].get("file_path", "") if issues else "",
                agent_type="fixer_agent",
            )
            if hitl_ctx.applicable_constraints:
                hitl_constraints_section += "\n--- HITL CONSTRAINTS (MUST FOLLOW) ---\n"
                for c in hitl_ctx.applicable_constraints:
                    hitl_constraints_section += f"Rule {c.rule_id}:\n"
                    if c.description:
                        hitl_constraints_section += f"  Description: {c.description}\n"
                    if c.standard_remediation:
                        hitl_constraints_section += f"  Standard Fix: {c.standard_remediation}\n"
                    hitl_constraints_section += f"  REQUIRED Action: {c.llm_action}\n"
                    if c.reasoning:
                        hitl_constraints_section += f"  Reasoning: {c.reasoning}\n"
                    hitl_constraints_section += "\n"

            if hitl_ctx.relevant_feedback:
                hitl_constraints_section += "--- PAST REVIEWER DECISIONS ---\n"
                for fb in hitl_ctx.relevant_feedback[:3]:
                    hitl_constraints_section += (
                        f"  File: {fb.file_path}, Action: {fb.human_action}"
                    )
                    if fb.human_feedback_text:
                        hitl_constraints_section += f", Feedback: \"{fb.human_feedback_text}\""
                    hitl_constraints_section += "\n"
                hitl_constraints_section += "\n"

            if hitl_ctx.suggestions_from_history:
                suggestions_text = "\n".join(
                    f"- {s}" for s in hitl_ctx.suggestions_from_history
                )
                hitl_constraints_section += f"--- PAST SUGGESTIONS ---\n{suggestions_text}\n\n"

        # ── Constraint Injection ────────────────────────────────
        prompt_constraints_section = ""
        if constraints_context:
            prompt_constraints_section = f"""
            ========================================
            MANDATORY RESOLUTION RULES (HOW TO FIX)
            ========================================
            {constraints_context}
            ========================================
            """

        integrity_rules = self._construct_code_integrity_rules()

        return f"""
            You are a Secure {language} Refactoring Agent.

            {integrity_rules}

            GENERAL INSTRUCTIONS:
            1. Analyze the issue, dependencies, and user provided constraints.
            2. Do a thorough analysis and provide the OPTIMUM solution based on best industry standards.
            3. DO NOT introduce any other issues. Double check to make sure of this.
            4. DO NOT change the logic flow unrelated to the fix.
            5. **CRITICAL:** MAINTAIN code layout where possible. Do not shift code unnecessarily, as this fragment is part of a larger file.
            6. Verify your fix against the "EXTERNAL DEFINITIONS" provided above (e.g., check struct member names).
            7. Cross check with the original chunk provided to make sure integrity of the file is maintained.
            8. **CRITICAL** Return the code as raw text only. Do not use markdown code blocks, backticks, or language identifiers.
            9. **CRITICAL** Output the code directly. Do not wrap it in ``` or C/C++ tags.
            10. Preserve ALL blank lines from the original code. If the original has a blank line, your output MUST too.
            11. DO NOT add extra arguments to function calls unless you also update the function definition.
            12. DO NOT remove type declarations from for-loop initializers (e.g., keep 'int' in 'for (int i = 0; ...)').
            13. DO NOT use macros (A_ARRAY_SIZE, ARRAY_SIZE, A_COMPILE_TIME_ASSERT, etc.) unless visible in the code or context.

            Fix the reported issues in the "CODE FRAGMENT TO FIX" below.

            {prompt_constraints_section}

            {context_section}{hitl_constraints_section}

            --- ISSUES TO RESOLVE ---
            {issues_text}

            --- CODE FRAGMENT TO FIX ---
            ```{language}
            {content}
            ```

            CRITICAL OUTPUT INSTRUCTIONS:
            1. You MUST return the **ENTIRE** content of the "CODE FRAGMENT TO FIX" with the fixes applied.
            2. DO NOT use placeholder comments like "// ... existing code ...". Return the full code.
            3. Verify your fix against the "EXTERNAL DEFINITIONS" provided above (e.g., check struct member names).
            4. If you return truncated code, the system will REJECT your fix.
            5. Preserve ALL blank lines and formatting from the original. Do NOT merge lines by removing newlines.
            6. Verify argument counts in all function calls match their definitions.
            """

    def _load_directives(self) -> List[Dict]:
        """Load refactoring directives from JSONL file."""
        tasks = []
        if not self.directives_path.exists():
            return []
        try:
            with open(self.directives_path, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            tasks.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            self.logger.debug(f"Skipping malformed JSON line: {e}")
                            continue
        except Exception as e:
            self.logger.error(f"Failed to load directives file: {e}")
        return tasks

    def _group_by_file(self, directives: List[Dict]) -> Dict[str, List[Dict]]:
        """Group directives by file path."""
        grouped = {}
        for t in directives:
            if t.get('file_path'):
                grouped.setdefault(t['file_path'], []).append(t)
        return grouped

    def _smart_strip_code(self, text: str) -> str:
        """
        Remove excessive leading/trailing whitespace while preserving internal structure.

        Unlike .strip(), this method:
        - Strips only leading/trailing blank lines (up to 3 each)
        - Preserves ALL internal blank lines (critical for code structure around '}')
        - Does NOT destroy newlines between statements
        - Removes LLM preamble/postamble text artifacts
        """
        if not text:
            return text

        lines = text.split('\n')

        # Remove leading blank lines (max 3)
        start_idx = 0
        for idx in range(min(len(lines), 3)):
            if lines[idx].strip():
                break
            start_idx = idx + 1

        # Remove trailing blank lines (max 3)
        end_idx = len(lines)
        for idx in range(len(lines) - 1, max(len(lines) - 4, -1), -1):
            if lines[idx].strip():
                end_idx = idx + 1
                break
            end_idx = idx

        # Extract the preserved middle section
        result_lines = lines[start_idx:end_idx]

        # If we stripped everything, return original (safety)
        if not result_lines:
            return text

        return '\n'.join(result_lines)

    def _extract_code_from_response(self, response: str) -> Optional[str]:
        """Extract code from LLM response (handles markdown code blocks and plain code).

        Uses _smart_strip_code() instead of .strip() to preserve internal blank
        lines and code structure while removing only LLM preamble/postamble.
        """
        match = re.search(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
        if match:
            return self._smart_strip_code(match.group(1))
        if any(kw in response for kw in ["#include", "namespace", "class", "void ", "int ", "def ", "import "]):
            return self._smart_strip_code(response)
        return None

    def _validate_integrity(self, original: str, new: str) -> bool:
        """
        Validate that the new content is substantially similar to original.

        Uses multiple validation layers:
        1. Basic non-empty check
        2. Size check (new content must be >= 80% of original)

        Additional structural validation is done per-chunk in
        _validate_code_structure() and _validate_fix_diff() before
        the fix is accepted.
        """
        if not new.strip():
            return False
        # Size check: new content must be at least 80% of original
        return len(new) >= len(original) * 0.8

    # ------------------------------------------------------------------
    # Post-Fix Structural Validation (compilation safety net)
    # ------------------------------------------------------------------

    @staticmethod
    def _count_braces_outside_strings(text: str) -> Tuple[int, int]:
        """Count { and } braces, ignoring those inside strings, char literals,
        single-line comments (//), and multi-line comments (/* */).

        Returns (open_count, close_count).
        """
        open_count = 0
        close_count = 0
        in_string = False
        in_char = False
        in_line_comment = False
        in_block_comment = False
        i = 0
        length = len(text)

        while i < length:
            ch = text[i]
            prev_ch = text[i - 1] if i > 0 else ''

            # --- Block comment transitions ---
            if in_block_comment:
                if ch == '/' and prev_ch == '*':
                    in_block_comment = False
                i += 1
                continue
            # --- Line comment transitions ---
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            # --- String transitions ---
            if in_string:
                if ch == '"' and prev_ch != '\\':
                    in_string = False
                i += 1
                continue
            # --- Char literal transitions ---
            if in_char:
                if ch == "'" and prev_ch != '\\':
                    in_char = False
                i += 1
                continue

            # --- Detect comment/string starts ---
            if ch == '/' and i + 1 < length:
                next_ch = text[i + 1]
                if next_ch == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if next_ch == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch == "'":
                in_char = True
                i += 1
                continue

            # --- Count braces ---
            if ch == '{':
                open_count += 1
            elif ch == '}':
                close_count += 1

            i += 1

        return open_count, close_count

    def _validate_code_structure(self, original: str, fixed: str) -> Tuple[bool, str]:
        """
        Validate structural integrity of LLM-generated fix BEFORE accepting it.

        Returns (is_valid, message).

        Hard failures (is_valid=False) cause the fix to be rejected and the
        original chunk is preserved.  Soft warnings are logged but the fix
        is still accepted.

        Checks:
          1. Brace balance: {/} counts must match each other and original.
          2. Blank line preservation: >20% reduction is a warning.
          3. For-loop type declarations: count must not decrease.
          4. Merged lines: '}\\n' count must not decrease >10%.
        """
        warnings: List[str] = []

        # --- CHECK 1: BRACE BALANCE (hard failure only on mismatch) ---
        orig_open, orig_close = self._count_braces_outside_strings(original)
        fixed_open, fixed_close = self._count_braces_outside_strings(fixed)

        # Hard failure: fixed code has unbalanced braces (open != close)
        if fixed_open != fixed_close:
            return False, (
                f"Brace mismatch in fixed code: {fixed_open} '{{' vs "
                f"{fixed_close} '}}'. Reverting to original."
            )

        # Soft warning: brace count changed but is still balanced.
        # This is EXPECTED when the LLM adds error-handling blocks,
        # null checks, or other defensive code (e.g. if (...) { return; }).
        if orig_open == orig_close and fixed_open != orig_open:
            warnings.append(
                f"Brace pair count changed: original had {orig_open} pairs, "
                f"fixed has {fixed_open} (likely added/removed code blocks)."
            )

        # --- CHECK 2: BLANK LINE PRESERVATION (soft warning) ---
        orig_blank = original.count('\n\n')
        fixed_blank = fixed.count('\n\n')
        if orig_blank > 2 and fixed_blank < orig_blank * 0.8:
            warnings.append(
                f"Blank lines reduced from {orig_blank} to {fixed_blank} "
                f"(>{20}% loss — possible line merging around braces)."
            )

        # --- CHECK 3: FOR-LOOP TYPE DECLARATIONS (soft warning) ---
        # Matches patterns like:  for (int i =   or  for (size_t idx =
        for_typed_pat = re.compile(r'\bfor\s*\(\s*(?:unsigned\s+|signed\s+|const\s+)*\w+\s+\w+\s*[=;]')
        orig_typed_for = len(for_typed_pat.findall(original))
        fixed_typed_for = len(for_typed_pat.findall(fixed))
        if orig_typed_for > 0 and fixed_typed_for < orig_typed_for:
            warnings.append(
                f"For-loop type declarations reduced from {orig_typed_for} to "
                f"{fixed_typed_for}. Check if variable types were removed."
            )

        # --- CHECK 4: MERGED LINES around closing braces (soft warning) ---
        orig_brace_nl = original.count('}\n')
        fixed_brace_nl = fixed.count('}\n')
        if orig_brace_nl > 2 and fixed_brace_nl < orig_brace_nl * 0.9:
            warnings.append(
                f"Closing-brace newlines reduced from {orig_brace_nl} to "
                f"{fixed_brace_nl}. Lines may have been merged around '}}'."
            )

        return True, " | ".join(warnings) if warnings else ""

    def _validate_fix_diff(self, original: str, fixed: str, filename: str) -> str:
        """
        Compare original and fixed code for semantic consistency.

        Returns a warning string (empty string if no issues).
        This check is advisory only — it never rejects a fix.

        Checks:
          1. New macros introduced that weren't in the original.
          2. For-loop type removal (additional heuristic).
        """
        warnings: List[str] = []

        # --- CHECK 1: Suspicious new macros ---
        suspicious_macro_pat = re.compile(
            r'\b(A_ARRAY_SIZE|ARRAY_SIZE|A_COMPILE_TIME_ASSERT|'
            r'QDF_COMPILE_TIME_ASSERT|A_ASSERT|QDF_ASSERT)\b'
        )
        orig_macros = set(suspicious_macro_pat.findall(original))
        fixed_macros = set(suspicious_macro_pat.findall(fixed))
        new_macros = fixed_macros - orig_macros
        if new_macros:
            for macro in sorted(new_macros):
                warnings.append(
                    f"Macro '{macro}' introduced but not in original chunk. "
                    f"Verify it is defined and has correct argument count."
                )

        # --- CHECK 2: Type-cast style for-loops introduced ---
        # Detect: for (var = (CAST)val  — i.e., no type before variable
        cast_for_pat = re.compile(r'\bfor\s*\(\s*\w+\s*=\s*\(')
        orig_cast_for = len(cast_for_pat.findall(original))
        fixed_cast_for = len(cast_for_pat.findall(fixed))
        if fixed_cast_for > orig_cast_for:
            warnings.append(
                f"New cast-style for-loop initializers detected "
                f"({orig_cast_for} -> {fixed_cast_for}). "
                f"Check if type declarations were incorrectly removed."
            )

        return " | ".join(warnings) if warnings else ""

    def _atomic_write(self, file_path: Path, content: str):
        """
        [FIX] Write to temp file first, then move to ensure atomic save.
        """
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            shutil.move(tmp_path, file_path)
        except Exception as e:
            self.logger.error(f"Failed to write file {file_path}: {e}")
            if tmp_path.exists():
                os.remove(tmp_path)

    def _backup_file(self, file_path: Path):
        """Backup file before modification."""
        if self.dry_run:
            return
        try:
            if file_path.is_relative_to(self.codebase_root):
                rel_path = file_path.relative_to(self.codebase_root)
            else:
                rel_path = file_path.name

            dest_path = self.backup_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if not dest_path.exists():
                shutil.copy2(file_path, dest_path)
        except Exception as e:
            if self.verbose:
                self.logger.warning(f"    [!] Backup failed: {e}")

    def _save_report(self, results: List[Dict], filename: str) -> str:
        """
        Saves the execution report as a beautifully formatted Excel file.
        Uses ExcelWriter for consistent formatting.
        """
        output_path = str(Path(filename).resolve())
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if not results:
            return ""

        try:
            # Prepare summary metadata
            total_tasks = len(results)
            fixed_count = sum(1 for r in results if r.get('final_status') == 'FIXED')
            failed_count = sum(1 for r in results if 'FAIL' in str(r.get('final_status', '')))
            skipped_count = sum(1 for r in results if r.get('final_status') == 'SKIPPED')

            summary_metadata = {
                "Total Tasks": str(total_tasks),
                "Successfully Fixed": str(fixed_count),
                "Failed/Pending": str(failed_count),
                "Skipped": str(skipped_count),
                "Execution Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Codebase": str(self.codebase_root.name),
                "Mode": "DRY RUN" if self.dry_run else "LIVE"
            }

            # Reorder columns for better readability
            preferred_order = ['file_path', 'line_number', 'severity', 'final_status', 'rationale', 'suggested_fix', 'details']
            available_columns = list(dict.fromkeys([c for c in preferred_order if c in (results[0].keys() if results else [])] +
                                                     [c for c in results[0].keys() if c not in preferred_order]))

            # Create writer and add sheets
            writer = ExcelWriter(output_path)
            writer.add_data_sheet(summary_metadata, "Summary", "Codebase Fixer Execution Report")
            writer.add_table_sheet(available_columns, results, "Audit Log", status_column="final_status")

            # Add audit trail sheet if there are entries
            if self.audit_trail:
                audit_columns = [
                    "timestamp", "file_path", "line_number", "issue_type",
                    "severity", "source_type", "source_sheet", "action",
                    "final_status", "hitl_constraints", "human_feedback",
                    "details",
                ]
                writer.add_table_sheet(
                    audit_columns, self.audit_trail,
                    "Decision Trail", status_column="final_status",
                )

            writer.save()

            self.logger.info(f"Report saved to: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Excel report generation failed ({e}), falling back to JSON.")
            json_path = output_path.replace('.xlsx', '.json')
            try:
                with open(json_path, 'w') as f:
                    json.dump(results, f, indent=2, default=str)
                return json_path
            except Exception as e2:
                self.logger.error(f"JSON fallback also failed: {e2}")
                return ""

    def _trigger_email_report(self, recipients: List[str], attachment_path: str, results: List[Dict], file_count: int):
        """
        Sends a comprehensive email report with execution summary and Excel attachment.
        """
        try:
            total_tasks = len(results)
            fixed_count = sum(1 for r in results if r.get('final_status') == 'FIXED')
            failed_count = sum(1 for r in results if r.get('final_status') in ['LLM_FAIL', 'FILE_NOT_FOUND', 'SKIPPED'])

            modified_files_set = set()
            for r in results:
                if r.get('final_status') == 'FIXED':
                    modified_files_set.add(r.get('file_path', 'unknown'))

            modified_count = len(modified_files_set)

            mod_files_list = sorted([Path(p).name for p in modified_files_set])
            if len(mod_files_list) > 5:
                files_display = ", ".join(mod_files_list[:5]) + f", and {len(mod_files_list)-5} others."
            elif mod_files_list:
                files_display = ", ".join(mod_files_list)
            else:
                files_display = "None"

            end_time = datetime.now()
            duration_str = str(end_time - self.start_time).split('.')[0]

            metadata = {
                "Agent Type": "CodebaseFixerAgent (GenAI)",
                "Execution Mode": "DRY RUN (Simulation)" if self.dry_run else "LIVE (Changes Applied)",
                "Project Root": self.codebase_root.name,
                "Full Path": str(self.codebase_root),
                "Start Time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "End Time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Duration": duration_str,
                "Backup Location": str(self.backup_dir) if not self.dry_run else "N/A"
            }

            stats = {
                "Files Modified": str(modified_count),
                "Total Tasks": str(total_tasks),
                "Fixed Successfully": str(fixed_count),
                "Pending/Failed": str(failed_count)
            }

            analysis_summary = (
                f"The agent successfully analyzed {file_count} files and applied {fixed_count} automated fixes "
                f"across {modified_count} files. Files updated: {files_display}. "
                f"{failed_count} items require manual review."
            )

            reporter = EmailReporter()
            success = reporter.send_report(
                recipients=recipients,
                metadata=metadata,
                stats=stats,
                analysis_summary=analysis_summary,
                attachment_path=attachment_path
            )

            if success:
                self.logger.info(f"Report email sent to {', '.join(recipients)}")
            else:
                self.logger.warning("Report email delivery failed.")

        except Exception as e:
            self.logger.error(f"Report trigger failed: {e}")

    def get_results(self) -> Dict:
        """
        Get current execution results. Can be called after run_agent for pipeline integration.

        Returns:
            Dictionary with execution metadata and results
        """
        return {
            "project": str(self.codebase_root),
            "start_time": self.start_time.isoformat(),
            "dry_run": self.dry_run,
            "output_dir": self.output_dir,
            "audit_trail": self.audit_trail,
        }

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def _audit_decision(
        self,
        task: Dict,
        final_status: str,
        details: str = "",
    ) -> None:
        """Record an audit entry for a processed task.

        Each entry captures: timestamp, file, line, issue_type,
        source_type, action, final_status, hitl_constraints, and
        free-form details.
        """
        hitl_constraints = ""
        if self.hitl_context:
            try:
                ctx = self.hitl_context.get_augmented_context(
                    issue_type=task.get("issue_type", ""),
                    file_path=task.get("file_path", ""),
                    agent_type="fixer_agent",
                )
                if ctx.applicable_constraints:
                    hitl_constraints = "; ".join(
                        f"{c.rule_id}: {c.llm_action}"
                        for c in ctx.applicable_constraints
                    )
            except Exception:
                pass

        entry = {
            "timestamp": datetime.now().isoformat(),
            "file_path": task.get("file_path", ""),
            "line_number": task.get("line_number", 0),
            "issue_type": task.get("issue_type", ""),
            "severity": task.get("severity", ""),
            "source_type": task.get("source_type", "unknown"),
            "source_sheet": task.get("source_sheet", ""),
            "action": task.get("action", "FIX"),
            "final_status": final_status,
            "hitl_constraints": hitl_constraints,
            "human_feedback": task.get("human_feedback", ""),
            "details": details,
        }
        self.audit_trail.append(entry)