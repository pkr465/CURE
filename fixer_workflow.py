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

    Supports three modes:

    1. **Fixer mode** (default): Human Review (Excel) → Parsing Logic → CodebaseFixerAgent
    2. **Batch-patch mode** (``--batch-patch``): Multi-file patch → CodebaseBatchPatchAgent
    3. **Patch-analysis mode** (``--patch-file`` + ``--patch-target``): Single-file patch analysis → CodebasePatchAgent
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

        # Excel-related paths (only used in fixer mode)
        is_patch_mode = self.batch_patch_file or (self.patch_file and self.patch_target)
        self.excel_path = Path(args.excel_file).resolve() if not is_patch_mode else None
        self.directives_jsonl = self.workspace_dir / "agent_directives.jsonl"
        self.final_report = self.workspace_dir / "final_execution_audit.xlsx"

        # Initialize GlobalConfig
        self.global_config = self._initialize_global_config()

        # Resolve codebase_root: CLI arg → GlobalConfig → default
        # Match the same resolution order as main.py
        cli_codebase = args.codebase_path
        if cli_codebase == "codebase" and self.global_config:
            # CLI was left at default — try GlobalConfig
            config_path = self.global_config.get_path("paths.code_base_path")
            if config_path:
                cli_codebase = config_path
        self.codebase_root = Path(cli_codebase).resolve()

        # Ensure workspace exists
        os.makedirs(self.workspace_dir, exist_ok=True)

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
                return GlobalConfig()  # auto-loads global_config.yaml
        except Exception as e:
            if self.args.verbose:
                print(f"    [WARNING] Could not load GlobalConfig: {e}")
            return None

    def execute(self):
        """
        Execute the workflow. Dispatches to the appropriate mode based
        on CLI arguments:

        - ``--patch-file`` + ``--patch-target`` → patch analysis
        - ``--batch-patch``                     → batch patch
        - (default)                             → fixer (Excel → agent)
        """
        if self.patch_file and self.patch_target:
            return self._execute_patch_analysis()
        if self.batch_patch_file:
            return self._execute_batch_patch()
        return self._execute_fixer()

    # ─── Patch-analysis mode ─────────────────────────────────────────

    def _execute_patch_analysis(self):
        """Analyse a single-file patch using CodebasePatchAgent.

        Requires ``--patch-file`` (the diff) and ``--patch-target`` (the
        original source file).  The agent runs LLM + optional static
        analysis on both original and patched versions, then diffs findings
        to identify issues *introduced* by the patch.
        """
        print("=" * 60)
        print(" Patch Analysis Workflow")
        print("=" * 60)

        patch_path = Path(self.patch_file).resolve()
        target_path = Path(self.patch_target).resolve()

        # Validate inputs
        if not patch_path.exists():
            print(f"[!] Error: Patch file does not exist: {patch_path}")
            return
        if not target_path.exists():
            print(f"[!] Error: Target source file does not exist: {target_path}")
            return

        print(f"    Target file: {target_path}")
        print(f"    Patch file:  {patch_path}")

        try:
            from agents.codebase_patch_agent import CodebasePatchAgent
        except ImportError as e:
            print(f"[!] Error: Could not import CodebasePatchAgent: {e}")
            return

        # Resolve LLM tools
        llm_tools = self._build_llm_tools()

        # Resolve codebase path for header/context resolution
        patch_codebase = getattr(self.args, "patch_codebase_path", None)
        if not patch_codebase:
            # Fall back to --codebase-path, then parent of target file
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

            excel_path = str(self.workspace_dir / "detailed_code_review.xlsx")
            result = agent.run_analysis(excel_path=excel_path)

            print(f"\n    Patch Analysis Complete!")
            print(f"    Original issues: {result.get('original_issue_count', 0)}")
            print(f"    Patched issues:  {result.get('patched_issue_count', 0)}")
            print(f"    NEW issues:      {result.get('new_issue_count', 0)}")
            print(f"    Excel output:    {result.get('excel_path', 'N/A')}")

        except Exception as e:
            print(f"    [!] Patch Analysis failed: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()

        print("\n" + "=" * 60)
        print(" PATCH ANALYSIS COMPLETE")
        print("=" * 60)

    # ─── Batch-patch mode ─────────────────────────────────────────────

    def _execute_batch_patch(self):
        """Apply a multi-file patch using CodebaseBatchPatchAgent."""
        print("=" * 60)
        print(" Batch Patch Workflow")
        print("=" * 60)

        patch_path = Path(self.batch_patch_file).resolve()

        # Validate inputs
        if not patch_path.exists():
            print(f"[!] Error: Patch file does not exist: {patch_path}")
            return
        if not self.codebase_root.exists():
            print(f"[!] Error: Codebase path does not exist: {self.codebase_root}")
            return

        try:
            from agents.codebase_batch_patch_agent import CodebaseBatchPatchAgent
        except ImportError as e:
            print(f"[!] Error: Could not import CodebaseBatchPatchAgent: {e}")
            return

        try:
            agent = CodebaseBatchPatchAgent(
                patch_file=str(patch_path),
                codebase_path=str(self.codebase_root),
                output_dir=str(self.workspace_dir),
                config=self.global_config,
                dry_run=self.args.dry_run,
                verbose=self.args.verbose,
            )
            result = agent.run()

            if result and self.args.verbose:
                print(f"    [OK] Batch patch complete. Result: {result}")

        except Exception as e:
            print(f"    [!] Exception during batch patch: {e}")
            if self.args.verbose:
                import traceback
                traceback.print_exc()

        print("\n" + "=" * 60)
        print(" BATCH PATCH COMPLETE")
        print(f" Output: {self.workspace_dir / 'patched_files'}")
        print("=" * 60)

    # ─── Fixer mode (default) ─────────────────────────────────────────

    def _execute_fixer(self):
        """Two-step workflow: parse Excel → run CodebaseFixerAgent."""
        print("="*60)
        print(" Automated Codebase Repair Workflow")
        print("="*60)

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

        print("\n" + "="*60)
        print(" WORKFLOW COMPLETE")
        print(f" Report: {self.final_report}")
        print("="*60)

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
            # Generate the JSONL intermediate file with source filtering
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
            # Initialize the Agent with new DI pattern
            agent = CodebaseFixerAgent(
                codebase_root=str(self.codebase_root),
                directives_file=str(self.directives_jsonl),
                backup_dir=str(self.workspace_dir / "shelved_backups"),
                output_dir=str(self.workspace_dir),
                config=self.global_config,
                dry_run=self.args.dry_run,
                verbose=self.args.verbose
            )

            # Run the agent and generate the final audit report
            # email_recipients=None will resolve from config if needed
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
        help="Path to the reviewed Excel file (fixer mode)"
    )
    parser.add_argument(
        "--batch-patch",
        default=None,
        metavar="PATCH_FILE",
        help="Path to a multi-file patch file (=== header format). "
             "When provided, runs the Batch Patch Agent instead of the fixer."
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

    # --- Source Filtering ---
    parser.add_argument(
        "--fix-source",
        choices=["all", "llm", "static", "patch"],
        default="llm",
        help="Process only issues from a specific source: "
             "all (every sheet), llm (Analysis sheet), "
             "static (static_* sheets), patch (patch_* sheets)"
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

    # Initialize and Run
    workflow = HumanInTheLoopWorkflow(args)
    workflow.execute()
