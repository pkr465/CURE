import os
import sys
import argparse
from pathlib import Path

# Import helper classes
try:
    from agents.parsers.excel_to_agent_parser import ExcelToAgentParser
    from agents.codebase_fixer_agent import CodebaseFixerAgent
    from utils.common.llm_tools import LLMTools
    from utils.parsers.global_config_parser import GlobalConfig
except ImportError as e:
    print("[!] Error: Could not import required modules.")
    print(f"    Details: {e}")
    print("    Ensure 'agents' and 'utils' packages are in PYTHONPATH or current directory.")
    sys.exit(1)


class HumanInTheLoopWorkflow:
    """
    Orchestrator for the Automated Codebase Repair Workflow.

    All three modes follow the same two-step pipeline:

    **Step 1 — Analyse**: Identify issues (from Excel review, patch analysis,
    or batch patch analysis).

    **Step 2 — Fix**: Parse findings + human feedback from Excel → generate
    JSONL directives → run ``CodebaseFixerAgent`` to apply fixes → validate →
    write patched files to ``out/patched_files/`` → generate audit report.

    Modes
    -----
    1. **Fixer mode** (default):
       Excel (human-reviewed) → Parse directives → CodebaseFixerAgent

    2. **Patch mode** (``--patch-file`` + ``--patch-target``):
       Step 1: CodebasePatchAgent → ``detailed_code_review.xlsx`` (patch_* tabs)
       Step 2: Parse patch directives → CodebaseFixerAgent → fix & validate

    3. **Batch-patch mode** (``--batch-patch``):
       Step 1: CodebaseBatchPatchAgent → ``detailed_code_review.xlsx`` (patch_* tabs)
       Step 2: Parse patch directives → CodebaseFixerAgent → fix & validate

    Use ``--analyse-only`` to stop after Step 1 (analysis) without applying
    fixes.  Use ``--fix-only`` to skip analysis and run the fixer directly
    from a pre-existing Excel file.
    """

    def __init__(self, args):
        """
        Initialize the workflow with parsed CLI arguments.
        """
        self.args = args
        self.workspace_dir = Path(args.out_dir).resolve()

        # Mode flags
        self.batch_patch_file = getattr(args, "batch_patch", None)
        self.patch_file = getattr(args, "patch_file", None)
        self.patch_target = getattr(args, "patch_target", None)
        self.analyse_only = getattr(args, "analyse_only", False)
        self.fix_only = getattr(args, "fix_only", False)

        # Excel-related paths
        is_patch_mode = self.batch_patch_file or (self.patch_file and self.patch_target)
        if is_patch_mode:
            # In patch modes the analysis Excel is produced by Step 1
            self.excel_path = Path(args.excel_file).resolve() if self.fix_only else None
        else:
            self.excel_path = Path(args.excel_file).resolve()

        self.directives_jsonl = self.workspace_dir / "agent_directives.jsonl"
        self.final_report = self.workspace_dir / "final_execution_audit.xlsx"

        # Initialize GlobalConfig
        self.global_config = self._initialize_global_config()

        # Resolve codebase_root: CLI arg → GlobalConfig → default
        cli_codebase = args.codebase_path
        if cli_codebase == "codebase" and self.global_config:
            config_path = self.global_config.get_path("paths.code_base_path")
            if config_path:
                cli_codebase = config_path
        self.codebase_root = Path(cli_codebase).resolve()

        # Ensure workspace exists
        os.makedirs(self.workspace_dir, exist_ok=True)

    # ─── Shared helpers ──────────────────────────────────────────────

    def _build_llm_tools(self):
        """Resolve LLM model and build LLMTools instance.

        Resolution order: ``--llm-model`` CLI arg → ``global_config.yaml``
        ``llm.model`` → default LLMTools().
        """
        llm_model = getattr(self.args, "llm_model", None)
        if not llm_model and self.global_config:
            try:
                llm_model = self.global_config.get("llm.model")
            except Exception:
                llm_model = None
        try:
            return LLMTools(model=llm_model) if llm_model else LLMTools()
        except Exception as e:
            if self.args.verbose:
                print(f"    [WARNING] LLMTools init failed: {e}")
            return None

    def _initialize_global_config(self):
        """
        Load GlobalConfig from default or custom config file.
        Returns None if config cannot be loaded.
        """
        try:
            config_file = getattr(self.args, 'config_file', None)
            if config_file:
                return GlobalConfig(config_file=config_file)
            else:
                return GlobalConfig()
        except Exception as e:
            if self.args.verbose:
                print(f"    [WARNING] Could not load GlobalConfig: {e}")
            return None

    # ─── Shared fix pipeline (Step 2) ────────────────────────────────

    def _step_parse_and_fix(self, excel_path: str, fix_source: str = "patch",
                            step_prefix: str = "Step 2") -> dict:
        """Parse findings from Excel and run CodebaseFixerAgent to apply fixes.

        This is the shared Step 2 used by all three modes.

        Args:
            excel_path: Path to ``detailed_code_review.xlsx`` with findings.
            fix_source: Which sheets to parse — ``"patch"``, ``"llm"``,
                ``"static"``, or ``"all"``.
            step_prefix: Label prefix for console output (e.g. "Step 2/2").

        Returns:
            dict with fixer agent results, or empty dict on failure.
        """
        # -- Step 2a: Parse Excel into directives --------------------------------
        print(f"\n[{step_prefix}a] Parsing Findings from Excel: {Path(excel_path).name}")
        print(f"    Fix source filter: {fix_source}")

        excel_p = Path(excel_path)
        if not excel_p.exists():
            print(f"    [!] Error: Excel file not found: {excel_p}")
            return {}

        try:
            parser = ExcelToAgentParser(str(excel_p))
            directive_count = parser.generate_agent_directives(
                str(self.directives_jsonl),
                fix_source=fix_source,
            )

            if not self.directives_jsonl.exists() or directive_count == 0:
                print("    [!] No actionable directives found — nothing to fix.")
                print("    Tip: Review the Excel and add Feedback/Constraints "
                      "columns to guide the fixer.")
                return {}

            print(f"    [OK] Directives generated: {self.directives_jsonl} "
                  f"({directive_count} directives)")

        except Exception as e:
            print(f"    [!] Exception during Excel parsing: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return {}

        # -- Step 2b: Run CodebaseFixerAgent ------------------------------------
        print(f"\n[{step_prefix}b] Launching Fixer Agent")
        print(f"    Target Codebase: {self.codebase_root}")
        print(f"    Source Filter:   {fix_source}")

        try:
            agent = CodebaseFixerAgent(
                codebase_root=str(self.codebase_root),
                directives_file=str(self.directives_jsonl),
                backup_dir=str(self.workspace_dir / "shelved_backups"),
                output_dir=str(self.workspace_dir),
                config=self.global_config,
                dry_run=self.args.dry_run,
                verbose=self.args.verbose,
            )

            result = agent.run_agent(
                report_filename=str(self.final_report),
                email_recipients=None,
            )

            if result:
                status = result.get("status", "unknown")
                files_done = result.get("files_processed", 0)
                report = result.get("report_path", "N/A")
                print(f"\n    Fixer Agent: {status} — {files_done} file(s) processed")
                print(f"    Audit report: {report}")

            return result or {}

        except Exception as e:
            print(f"    [!] Exception during fixer agent execution: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return {}

    # ─── Dispatcher ──────────────────────────────────────────────────

    def execute(self):
        """
        Execute the workflow. Dispatches to the appropriate mode based
        on CLI arguments:

        - ``--patch-file`` + ``--patch-target`` → patch analyse + fix
        - ``--batch-patch``                     → batch patch analyse + fix
        - (default)                             → fixer (Excel → agent)
        """
        if self.patch_file and self.patch_target:
            return self._execute_patch_workflow()
        if self.batch_patch_file:
            return self._execute_batch_patch_workflow()
        return self._execute_fixer()

    # ─── Patch workflow (single-file) ────────────────────────────────

    def _execute_patch_workflow(self):
        """Two-step pipeline for single-file patch analysis and fixing.

        Step 1: Run ``CodebasePatchAgent.run_analysis()`` to identify issues
        introduced by the patch → writes findings to ``detailed_code_review.xlsx``
        (patch_* tabs).

        Step 2: Parse the Excel (patch directives + human feedback) → run
        ``CodebaseFixerAgent`` to apply fixes, validate, and write patched
        files to ``out/patched_files/``.

        Use ``--analyse-only`` to stop after Step 1.
        Use ``--fix-only`` to skip Step 1 and run the fixer from a
        pre-existing Excel file.
        """
        total_steps = "2" if not self.analyse_only and not self.fix_only else "1"

        print("=" * 60)
        print(" Patch Analyse & Fix Workflow")
        print("=" * 60)

        excel_path = str(self.workspace_dir / "detailed_code_review.xlsx")

        # -- Step 1: Analyse ---------------------------------------------------
        if not self.fix_only:
            patch_path = Path(self.patch_file).resolve()
            target_path = Path(self.patch_target).resolve()

            if not patch_path.exists():
                print(f"[!] Error: Patch file does not exist: {patch_path}")
                return
            if not target_path.exists():
                print(f"[!] Error: Target source file does not exist: {target_path}")
                return

            print(f"\n[Step 1/{total_steps}] Patch Analysis")
            print(f"    Target file: {target_path}")
            print(f"    Patch file:  {patch_path}")

            try:
                from agents.codebase_patch_agent import CodebasePatchAgent
            except ImportError as e:
                print(f"[!] Error: Could not import CodebasePatchAgent: {e}")
                return

            llm_tools = self._build_llm_tools()

            # Resolve codebase path for header/context resolution
            patch_codebase = getattr(self.args, "patch_codebase_path", None)
            if not patch_codebase:
                if self.codebase_root.exists() and str(self.codebase_root) != str(Path("codebase").resolve()):
                    patch_codebase = str(self.codebase_root)
                else:
                    patch_codebase = str(target_path.parent)

            enable_adapters = getattr(self.args, "enable_adapters", False)

            try:
                agent = CodebasePatchAgent(
                    file_path=str(target_path),
                    patch_file=str(patch_path),
                    output_dir=str(self.workspace_dir),
                    config=self.global_config,
                    llm_tools=llm_tools,
                    enable_adapters=enable_adapters,
                    verbose=self.args.verbose,
                    codebase_path=patch_codebase,
                )

                result = agent.run_analysis(excel_path=excel_path)

                print(f"\n    Analysis Complete:")
                print(f"    Original issues: {result.get('original_issue_count', 0)}")
                print(f"    Patched issues:  {result.get('patched_issue_count', 0)}")
                print(f"    NEW issues:      {result.get('new_issue_count', 0)}")
                print(f"    Excel output:    {result.get('excel_path', 'N/A')}")

                # Use the excel path from the result if available
                excel_path = result.get("excel_path", excel_path)

            except Exception as e:
                print(f"    [!] Patch Analysis failed: {e}")
                if self.args.verbose:
                    import traceback
                    traceback.print_exc()
                return

            if self.analyse_only:
                print("\n" + "=" * 60)
                print(" PATCH ANALYSIS COMPLETE (--analyse-only)")
                print(f" Excel: {excel_path}")
                print("=" * 60)
                return

        else:
            # --fix-only: use the provided or default Excel path
            excel_path = str(self.excel_path) if self.excel_path else excel_path
            print(f"\n[--fix-only] Skipping analysis, reading from: {excel_path}")

        # -- Step 2: Fix -------------------------------------------------------
        fix_source = getattr(self.args, "fix_source", "patch")
        self._step_parse_and_fix(
            excel_path=excel_path,
            fix_source=fix_source,
            step_prefix=f"Step 2/{total_steps}",
        )

        print("\n" + "=" * 60)
        print(" PATCH ANALYSE & FIX COMPLETE")
        print(f" Report: {self.final_report}")
        print(f" Patched files: {self.workspace_dir / 'patched_files'}")
        print("=" * 60)

    # ─── Batch-patch workflow (multi-file) ────────────────────────────

    def _execute_batch_patch_workflow(self):
        """Two-step pipeline for multi-file batch patch analysis and fixing.

        Step 1: Run ``CodebaseBatchPatchAgent.run()`` — analyses each file in
        the patch via ``CodebasePatchAgent`` and writes findings to
        ``detailed_code_review.xlsx`` (one ``patch_*`` tab per file).

        Step 2: Parse the Excel (patch directives + human feedback) → run
        ``CodebaseFixerAgent`` to apply fixes, validate, and write patched
        files to ``out/patched_files/``.

        Use ``--analyse-only`` to stop after Step 1.
        Use ``--fix-only`` to skip Step 1 and run the fixer from a
        pre-existing Excel file.
        """
        total_steps = "2" if not self.analyse_only and not self.fix_only else "1"

        print("=" * 60)
        print(" Batch Patch Analyse & Fix Workflow")
        print("=" * 60)

        excel_path = str(self.workspace_dir / "detailed_code_review.xlsx")

        # -- Step 1: Analyse ---------------------------------------------------
        if not self.fix_only:
            patch_path = Path(self.batch_patch_file).resolve()

            if not patch_path.exists():
                print(f"[!] Error: Patch file does not exist: {patch_path}")
                return
            if not self.codebase_root.exists():
                print(f"[!] Error: Codebase path does not exist: {self.codebase_root}")
                return

            print(f"\n[Step 1/{total_steps}] Batch Patch Analysis")
            print(f"    Patch file: {patch_path}")
            print(f"    Codebase:   {self.codebase_root}")

            try:
                from agents.codebase_batch_patch_agent import CodebaseBatchPatchAgent
            except ImportError as e:
                print(f"[!] Error: Could not import CodebaseBatchPatchAgent: {e}")
                return

            llm_tools = self._build_llm_tools()
            enable_adapters = getattr(self.args, "enable_adapters", False)

            try:
                agent = CodebaseBatchPatchAgent(
                    patch_file=str(patch_path),
                    codebase_path=str(self.codebase_root),
                    output_dir=str(self.workspace_dir),
                    config=self.global_config,
                    llm_tools=llm_tools,
                    enable_adapters=enable_adapters,
                    dry_run=self.args.dry_run,
                    verbose=self.args.verbose,
                )

                result = agent.run(excel_path=excel_path)

                print(f"\n    Analysis Complete:")
                print(f"    Files analysed:  {result.get('patched', 0)}")
                print(f"    Original issues: {result.get('original_issue_count', 0)}")
                print(f"    Patched issues:  {result.get('patched_issue_count', 0)}")
                print(f"    NEW issues:      {result.get('new_issue_count', 0)}")
                print(f"    Excel output:    {result.get('excel_path', 'N/A')}")

                # Use the excel path from the result if available
                excel_path = result.get("excel_path", excel_path)

            except Exception as e:
                print(f"    [!] Batch Patch Analysis failed: {e}")
                if self.args.verbose:
                    import traceback
                    traceback.print_exc()
                return

            if self.analyse_only:
                print("\n" + "=" * 60)
                print(" BATCH PATCH ANALYSIS COMPLETE (--analyse-only)")
                print(f" Excel: {excel_path}")
                print("=" * 60)
                return

        else:
            # --fix-only: use the provided or default Excel path
            excel_path = str(self.excel_path) if self.excel_path else excel_path
            print(f"\n[--fix-only] Skipping analysis, reading from: {excel_path}")

        # -- Step 2: Fix -------------------------------------------------------
        fix_source = getattr(self.args, "fix_source", "patch")
        self._step_parse_and_fix(
            excel_path=excel_path,
            fix_source=fix_source,
            step_prefix=f"Step 2/{total_steps}",
        )

        print("\n" + "=" * 60)
        print(" BATCH PATCH ANALYSE & FIX COMPLETE")
        print(f" Report: {self.final_report}")
        print(f" Patched files: {self.workspace_dir / 'patched_files'}")
        print("=" * 60)

    # ─── Fixer mode (default) ────────────────────────────────────────

    def _execute_fixer(self):
        """Two-step workflow: parse Excel → run CodebaseFixerAgent."""
        print("=" * 60)
        print(" Automated Codebase Repair Workflow")
        print("=" * 60)

        # Validate inputs before starting
        if not self.codebase_root.exists():
            print(f"[!] Error: Codebase path does not exist: {self.codebase_root}")
            return
        if not self.excel_path.exists():
            print(f"[!] Error: Excel file does not exist: {self.excel_path}")
            return

        # Step 1: Parse the Excel File
        if not self._step_parse_excel():
            print("[!] Workflow aborted at Step 1.")
            return

        # Step 2: Run the Fixer Agent
        self._step_run_agent()

        print("\n" + "=" * 60)
        print(" WORKFLOW COMPLETE")
        print(f" Report: {self.final_report}")
        print("=" * 60)

    def _step_parse_excel(self) -> bool:
        """
        Step 1: Parse the Excel review file and generate agent directives.

        Passes ``--fix-source`` filter to the parser so only issues from the
        selected source type(s) are included in the JSONL output.

        Returns:
            bool: True if successful, False otherwise
        """
        fix_source = getattr(self.args, "fix_source", "all")
        print(f"\n[Step 1/2] Parsing Human Review: {self.excel_path.name}")
        print(f"    Fix source filter: {fix_source}")

        try:
            parser = ExcelToAgentParser(str(self.excel_path))
            directive_count = parser.generate_agent_directives(
                str(self.directives_jsonl),
                fix_source=fix_source,
            )

            if not self.directives_jsonl.exists() or directive_count == 0:
                print("    [!] Error: JSONL file was not created or contains no directives.")
                return False

            print(f"    [OK] Directives generated: {self.directives_jsonl} ({directive_count} directives)")
            return True
        except Exception as e:
            print(f"    [!] Exception during parsing: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return False

    def _step_run_agent(self):
        """
        Step 2: Initialize and run the CodebaseFixerAgent using the new DI pattern.

        Uses GlobalConfig and LLMTools for configuration resolution:
        - CLI --llm-model takes precedence
        - Falls back to global_config.yaml llm.model setting
        - Uses default LLMTools() if neither is specified
        """
        fix_source = getattr(self.args, "fix_source", "all")
        print(f"\n[Step 2/2] Launching Fixer Agent")
        print(f"    Target Codebase: {self.codebase_root}")
        print(f"    Source Filter: {fix_source}")

        # Resolve LLM model from CLI arg or GlobalConfig
        llm_model = self.args.llm_model
        if not llm_model and self.global_config:
            try:
                llm_model = self.global_config.get("llm.model")
            except Exception:
                llm_model = None

        try:
            agent = CodebaseFixerAgent(
                codebase_root=str(self.codebase_root),
                directives_file=str(self.directives_jsonl),
                backup_dir=str(self.workspace_dir / "shelved_backups"),
                output_dir=str(self.workspace_dir),
                config=self.global_config,
                dry_run=self.args.dry_run,
                verbose=self.args.verbose
            )

            result = agent.run_agent(
                report_filename=str(self.final_report),
                email_recipients=None
            )

            if result and self.args.verbose:
                print(f"    [OK] Agent execution complete. Result: {result}")

        except Exception as e:
            print(f"    [!] Exception during agent execution: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()


# ==========================================
# Command Line Interface
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the Automated Codebase Repair Workflow using Human Feedback.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # --- File Paths ---
    parser.add_argument(
        "--excel-file",
        default="out/detailed_code_review.xlsx",
        help="Path to the reviewed Excel file (fixer mode, or --fix-only)"
    )
    parser.add_argument(
        "--batch-patch",
        default=None,
        metavar="PATCH_FILE",
        help="Path to a multi-file patch file. "
             "Step 1: analyses each file via CodebasePatchAgent. "
             "Step 2: parses findings + feedback and applies fixes."
    )
    parser.add_argument(
        "--patch-file",
        default=None,
        help="Path to a .patch/.diff file for single-file patch analysis "
             "(unified or normal diff format). Requires --patch-target."
    )
    parser.add_argument(
        "--patch-target",
        default=None,
        help="Path to the original source file being patched "
             "(used with --patch-file)"
    )
    parser.add_argument(
        "--patch-codebase-path",
        default=None,
        help="Root of the codebase for header/context resolution during "
             "patch analysis (defaults to --codebase-path or parent of --patch-target)"
    )
    parser.add_argument(
        "--enable-adapters",
        action="store_true",
        help="Enable deep static analysis adapters (Lizard, Flawfinder, CCLS) "
             "for patch analysis mode"
    )
    parser.add_argument(
        "--codebase-path",
        default="codebase",
        help="Root directory of the source code"
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Directory for output/patched files"
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help="Path to custom global_config.yaml file (overrides default)"
    )

    # --- Pipeline Control ---
    pipeline_group = parser.add_argument_group("Pipeline Control")
    pipeline_group.add_argument(
        "--analyse-only",
        action="store_true",
        help="Run only the analysis step (Step 1) without applying fixes. "
             "Produces the Excel report for human review. "
             "Applies to --patch-file and --batch-patch modes."
    )
    pipeline_group.add_argument(
        "--fix-only",
        action="store_true",
        help="Skip analysis and run the fixer directly from a pre-existing "
             "Excel file (--excel-file). Use after reviewing the analysis "
             "output and adding Feedback/Constraints columns."
    )

    # --- Source Filtering ---
    parser.add_argument(
        "--fix-source",
        choices=["all", "llm", "static", "patch"],
        default="patch",
        help="Process only issues from a specific source: "
             "all (every sheet), llm (Analysis sheet), "
             "static (static_* sheets), patch (patch_* sheets). "
             "Default is 'patch' for patch/batch-patch modes."
    )

    # --- LLM Configuration ---
    llm_group = parser.add_argument_group("LLM Configuration")
    llm_group.add_argument(
        "--llm-model",
        default=None,
        help="LLM model in 'provider::model' format "
             "(e.g., 'anthropic::claude-sonnet-4-20250514'). "
             "Overrides global_config.yaml llm.model setting."
    )
    llm_group.add_argument(
        "--llm-api-key",
        default=None,
        help="API Key (overrides env vars)"
    )
    llm_group.add_argument(
        "--llm-max-tokens",
        type=int,
        default=15000,
        help="Token limit for context"
    )
    llm_group.add_argument(
        "--llm-temperature",
        type=float,
        default=0.1,
        help="Sampling temperature"
    )

    # --- Safety & Debugging ---
    safe_group = parser.add_argument_group("Safety & Debugging")
    safe_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate fixes without writing to disk"
    )
    safe_group.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed logging"
    )
    safe_group.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )

    args = parser.parse_args()

    # Validate conflicting flags
    if getattr(args, "analyse_only", False) and getattr(args, "fix_only", False):
        parser.error("--analyse-only and --fix-only are mutually exclusive.")

    # In default fixer mode, override fix_source to 'llm' if user didn't
    # explicitly set it and we're not in a patch mode
    is_patch_mode = args.batch_patch or (args.patch_file and args.patch_target)
    if not is_patch_mode and args.fix_source == "patch":
        # User likely left the default — switch to "llm" for fixer mode
        args.fix_source = "llm"

    # Initialize and Run
    workflow = HumanInTheLoopWorkflow(args)
    workflow.execute()
