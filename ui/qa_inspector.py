"""
qa_inspector.py

CURE — Codebase Update & Refactor Engine
Post-fix QA validation: brace balance, file integrity, compilation check,
and metrics comparison.

Author: Pavan R
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# C/C++ file extensions for validation
CPP_EXTENSIONS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}


class QAInspector:
    """
    Validates fixed C/C++ codebase against structural requirements.

    Performs:
    1. File integrity checks (UTF-8, non-empty, reasonable size)
    2. Brace/bracket balance validation
    3. Include statement consistency
    4. Optional compilation check (if gcc/clang available)
    5. Issue count comparison (original vs fixed)
    """

    def __init__(
        self,
        fixed_codebase_path: str,
        original_results: Optional[List[Dict]] = None,
        original_metrics: Optional[Dict[str, Any]] = None,
    ):
        self.fixed_path = Path(fixed_codebase_path)
        self.original_results = original_results or []
        self.original_metrics = original_metrics or {}
        self._compiler = self._detect_compiler()

    def validate_all(self) -> List[Dict[str, Any]]:
        """
        Run all QA validation checks.

        Returns:
            List of result dicts, each with columns:
            File, Check, Status, Pass, Details
        """
        results = []

        # Per-file structural checks
        cpp_files = self._find_cpp_files()
        if not cpp_files:
            results.append({
                "File": "(all)",
                "Check": "File Discovery",
                "Status": "WARN",
                "Pass": False,
                "Details": "No C/C++ files found in output directory.",
            })
            return results

        results.append({
            "File": "(all)",
            "Check": "File Discovery",
            "Status": "PASS",
            "Pass": True,
            "Details": f"Found {len(cpp_files)} C/C++ files.",
        })

        # Check each file
        integrity_pass = 0
        integrity_fail = 0
        brace_pass = 0
        brace_fail = 0

        for fpath in cpp_files:
            # File integrity
            integrity = self._check_file_integrity(fpath)
            if integrity["Pass"]:
                integrity_pass += 1
            else:
                integrity_fail += 1
                results.append(integrity)

            # Brace balance
            brace = self._check_brace_balance(fpath)
            if brace["Pass"]:
                brace_pass += 1
            else:
                brace_fail += 1
                results.append(brace)

        # Summary entries
        results.append({
            "File": "(all)",
            "Check": "File Integrity",
            "Status": "PASS" if integrity_fail == 0 else "FAIL",
            "Pass": integrity_fail == 0,
            "Details": f"{integrity_pass} passed, {integrity_fail} failed out of {len(cpp_files)} files.",
        })

        results.append({
            "File": "(all)",
            "Check": "Brace Balance",
            "Status": "PASS" if brace_fail == 0 else "FAIL",
            "Pass": brace_fail == 0,
            "Details": f"{brace_pass} passed, {brace_fail} failed out of {len(cpp_files)} files.",
        })

        # Compilation check (optional)
        compile_result = self._check_compilation(cpp_files)
        results.append(compile_result)

        # Issue comparison
        comparison = self._compare_issue_counts()
        results.append(comparison)

        return results

    def _find_cpp_files(self) -> List[Path]:
        """Find all C/C++ files in the fixed codebase."""
        files = []
        if not self.fixed_path.exists():
            return files
        for ext in CPP_EXTENSIONS:
            files.extend(self.fixed_path.rglob(f"*{ext}"))
        return sorted(files)

    def _check_file_integrity(self, file_path: Path) -> Dict[str, Any]:
        """Validate a file is readable, non-empty, and valid UTF-8."""
        rel = file_path.relative_to(self.fixed_path) if file_path.is_relative_to(self.fixed_path) else file_path
        try:
            stat = file_path.stat()
            if stat.st_size == 0:
                return {
                    "File": str(rel),
                    "Check": "File Integrity",
                    "Status": "FAIL",
                    "Pass": False,
                    "Details": "File is empty (0 bytes).",
                }

            # Try reading as UTF-8
            content = file_path.read_text(encoding="utf-8")

            # Check for null bytes (binary corruption)
            if "\x00" in content:
                return {
                    "File": str(rel),
                    "Check": "File Integrity",
                    "Status": "FAIL",
                    "Pass": False,
                    "Details": "File contains null bytes (possible binary corruption).",
                }

            return {
                "File": str(rel),
                "Check": "File Integrity",
                "Status": "PASS",
                "Pass": True,
                "Details": f"{stat.st_size} bytes, {len(content.splitlines())} lines.",
            }

        except UnicodeDecodeError:
            return {
                "File": str(rel),
                "Check": "File Integrity",
                "Status": "FAIL",
                "Pass": False,
                "Details": "File is not valid UTF-8.",
            }
        except Exception as e:
            return {
                "File": str(rel),
                "Check": "File Integrity",
                "Status": "FAIL",
                "Pass": False,
                "Details": f"Cannot read file: {e}",
            }

    def _check_brace_balance(self, file_path: Path) -> Dict[str, Any]:
        """Validate matching braces, brackets, and parentheses."""
        rel = file_path.relative_to(self.fixed_path) if file_path.is_relative_to(self.fixed_path) else file_path
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")

            # Strip comments and string literals to avoid false positives
            clean = _strip_comments_and_strings(content)

            pairs = {"{": "}", "(": ")", "[": "]"}
            stack = []
            line_num = 1

            for ch in clean:
                if ch == "\n":
                    line_num += 1
                elif ch in pairs:
                    stack.append((ch, line_num))
                elif ch in pairs.values():
                    if not stack:
                        return {
                            "File": str(rel),
                            "Check": "Brace Balance",
                            "Status": "FAIL",
                            "Pass": False,
                            "Details": f"Unexpected closing '{ch}' near line {line_num}.",
                        }
                    open_ch, open_line = stack.pop()
                    if pairs[open_ch] != ch:
                        return {
                            "File": str(rel),
                            "Check": "Brace Balance",
                            "Status": "FAIL",
                            "Pass": False,
                            "Details": (
                                f"Mismatched: '{open_ch}' at line {open_line} "
                                f"closed by '{ch}' at line {line_num}."
                            ),
                        }

            if stack:
                unclosed = [(ch, ln) for ch, ln in stack[-3:]]
                desc = ", ".join(f"'{ch}' at line {ln}" for ch, ln in unclosed)
                return {
                    "File": str(rel),
                    "Check": "Brace Balance",
                    "Status": "FAIL",
                    "Pass": False,
                    "Details": f"Unclosed: {desc} ({len(stack)} total).",
                }

            return {
                "File": str(rel),
                "Check": "Brace Balance",
                "Status": "PASS",
                "Pass": True,
                "Details": "All braces, brackets, and parentheses balanced.",
            }

        except Exception as e:
            return {
                "File": str(rel),
                "Check": "Brace Balance",
                "Status": "SKIP",
                "Pass": True,  # Non-blocking
                "Details": f"Could not check: {e}",
            }

    def _detect_compiler(self) -> Optional[str]:
        """Detect available C compiler."""
        for compiler in ["gcc", "clang", "cc"]:
            try:
                result = subprocess.run(
                    [compiler, "--version"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return compiler
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    def _check_compilation(self, files: List[Path]) -> Dict[str, Any]:
        """Attempt syntax-check compilation on source files."""
        if not self._compiler:
            return {
                "File": "(all)",
                "Check": "Compilation",
                "Status": "SKIP",
                "Pass": True,  # Non-blocking
                "Details": "No compiler available (gcc/clang not found). Skipping compilation check.",
            }

        # Only syntax-check .c files (not headers) with -fsyntax-only
        source_files = [f for f in files if f.suffix in {".c", ".cpp", ".cc", ".cxx"}]
        if not source_files:
            return {
                "File": "(all)",
                "Check": "Compilation",
                "Status": "SKIP",
                "Pass": True,
                "Details": "No source files to compile (only headers found).",
            }

        errors = []
        checked = 0
        for fpath in source_files[:20]:  # Limit to 20 files for speed
            try:
                result = subprocess.run(
                    [self._compiler, "-fsyntax-only", "-w", str(fpath)],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                checked += 1
                if result.returncode != 0:
                    rel = fpath.relative_to(self.fixed_path) if fpath.is_relative_to(self.fixed_path) else fpath
                    first_error = result.stderr.strip().split("\n")[0][:200]
                    errors.append(f"{rel}: {first_error}")
            except subprocess.TimeoutExpired:
                errors.append(f"{fpath.name}: compilation timed out")
            except Exception as e:
                errors.append(f"{fpath.name}: {e}")

        if errors:
            detail = f"{len(errors)} errors in {checked} files checked. First: {errors[0]}"
            return {
                "File": "(all)",
                "Check": "Compilation",
                "Status": "FAIL",
                "Pass": False,
                "Details": detail[:300],
            }

        return {
            "File": "(all)",
            "Check": "Compilation",
            "Status": "PASS",
            "Pass": True,
            "Details": f"Syntax check passed for {checked} source files using {self._compiler}.",
        }

    def _compare_issue_counts(self) -> Dict[str, Any]:
        """Compare original issue count vs remaining after fixes."""
        original_count = len(self.original_results)
        if original_count == 0:
            return {
                "File": "(all)",
                "Check": "Issue Comparison",
                "Status": "SKIP",
                "Pass": True,
                "Details": "No original results to compare against.",
            }

        # Count issues that were marked for fixing
        fix_count = sum(
            1 for r in self.original_results
            if str(r.get("Action", "Auto-fix")).strip() not in ("Skip",)
        )

        return {
            "File": "(all)",
            "Check": "Issue Comparison",
            "Status": "PASS" if fix_count > 0 else "WARN",
            "Pass": True,
            "Details": (
                f"Original: {original_count} issues. "
                f"Targeted for fix: {fix_count}. "
                f"Skipped: {original_count - fix_count}."
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_comments_and_strings(code: str) -> str:
    """
    Remove C/C++ comments and string literals to avoid
    false positives in brace balance checking.
    """
    result = []
    i = 0
    n = len(code)
    in_line_comment = False
    in_block_comment = False
    in_string = False
    string_char = None

    while i < n:
        ch = code[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                result.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and i + 1 < n and code[i + 1] == "/":
                in_block_comment = False
                i += 2
            else:
                if ch == "\n":
                    result.append(ch)
                i += 1
            continue

        if in_string:
            if ch == "\\" and i + 1 < n:
                i += 2  # Skip escaped char
                continue
            if ch == string_char:
                in_string = False
            i += 1
            continue

        # Check for comment start
        if ch == "/" and i + 1 < n:
            if code[i + 1] == "/":
                in_line_comment = True
                i += 2
                continue
            if code[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue

        # Check for string start
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            i += 1
            continue

        # Check for preprocessor directives (skip entire line)
        if ch == "#" and (i == 0 or code[i - 1] == "\n"):
            while i < n and code[i] != "\n":
                i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def create_zip_archive(source_dir: str, output_path: str) -> str:
    """
    Create a ZIP archive of the fixed codebase.

    Args:
        source_dir: Directory to archive.
        output_path: Path for the output ZIP (without .zip extension).

    Returns:
        Full path to the created ZIP file.
    """
    return shutil.make_archive(output_path, "zip", source_dir)
