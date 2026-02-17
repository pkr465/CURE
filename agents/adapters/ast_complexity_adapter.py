"""AST-level complexity analysis using Lizard."""

import logging
from typing import Any, Dict, List, Optional

from agents.adapters.base_adapter import BaseStaticAdapter

# Try to import lizard at module level
try:
    import lizard
    LIZARD_AVAILABLE = True
except ImportError:
    LIZARD_AVAILABLE = False


class ASTComplexityAdapter(BaseStaticAdapter):
    """
    Analyzes C/C++ code complexity using Lizard's AST-based metrics.

    Measures cyclomatic complexity, nesting depth, parameter count, function length.
    Flags high-complexity functions and deep nesting as red flags.
    """

    def __init__(self, debug: bool = False):
        """
        Initialize AST complexity adapter.

        Args:
            debug: Enable debug logging if True.
        """
        super().__init__("ast_complexity", debug=debug)
        self.lizard_available = LIZARD_AVAILABLE
        if not self.lizard_available:
            self.logger.warning(
                "Lizard not available. Install with: pip install lizard"
            )

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze C/C++ files for complexity metrics.

        Args:
            file_cache: List of file entries with "file_relative_path" and "source" keys.
            ccls_navigator: Optional CCLS navigator (unused here).
            dependency_graph: Optional dependency graph (unused here).

        Returns:
            Standard analysis result dict with score, grade, metrics, issues, details.
        """
        # Step 1: Check tool availability
        if not self.lizard_available:
            return self._handle_tool_unavailable(
                "Lizard", "Install with: pip install lizard"
            )

        # Step 2: Filter to C/C++ files
        c_cpp_suffixes = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx")
        cpp_files = [
            entry
            for entry in file_cache
            if entry.get("file_relative_path", "").endswith(c_cpp_suffixes)
        ]

        if not cpp_files:
            return self._empty_result("No C/C++ files to analyze")

        # Step 3: Analyze each file and collect function metrics
        all_functions = []
        for entry in cpp_files:
            file_path = entry.get("file_relative_path", "unknown")
            source_code = entry.get("source", "")

            try:
                # Use Lizard to analyze the file
                file_info = lizard.analyze_file.analyze_source_code(
                    file_path, source_code
                )

                # Extract function metrics from the analyzed file
                for func in file_info.function_list:
                    func_dict = {
                        "file": file_path,
                        "name": func.name,
                        "long_name": getattr(func, "long_name", func.name),
                        "start_line": func.start_line,
                        "end_line": func.end_line,
                        "length": func.length,
                        "cyclomatic_complexity": func.cyclomatic_complexity,
                        "token_count": getattr(func, "token_count", 0),
                        "parameter_count": func.parameter_count,
                        "max_nesting_depth": getattr(func, "max_nesting_depth", 0),
                    }
                    all_functions.append(func_dict)

                if self.debug:
                    self.logger.debug(
                        f"Analyzed {file_path}: {len(file_info.function_list)} functions"
                    )

            except Exception as e:
                self.logger.error(f"Error analyzing {file_path}: {e}")
                continue

        if not all_functions:
            return self._empty_result("No functions found in C/C++ files")

        # Step 4: Identify flagged functions
        flagged_functions = []
        details = []
        high_cc_count = 0
        critical_cc_count = 0
        deep_nesting_count = 0
        many_params_count = 0

        for func in all_functions:
            cc = func["cyclomatic_complexity"]
            nesting = func["max_nesting_depth"]
            params = func["parameter_count"]
            loc = func["length"]

            flagged = False
            issue_strs = []

            # Flag high cyclomatic complexity
            if cc > 25:
                critical_cc_count += 1
                flagged = True
                issue_strs.append(f"critical complexity ({cc})")
            elif cc > 15:
                high_cc_count += 1
                flagged = True
                issue_strs.append(f"high complexity ({cc})")

            # Flag deep nesting
            if nesting > 4:
                deep_nesting_count += 1
                flagged = True
                issue_strs.append(f"deep nesting (depth {nesting})")

            # Flag many parameters
            if params > 6:
                many_params_count += 1
                flagged = True
                issue_strs.append(f"many parameters ({params})")

            # Flag long functions
            if loc > 150:
                flagged = True
                issue_strs.append(f"long function ({loc} LOC)")

            if flagged:
                description = f"{func['long_name']}: {', '.join(issue_strs)}"
                detail = self._make_detail(
                    file=func["file"],
                    function=func["long_name"],
                    line=func["start_line"],
                    description=description,
                    severity=self._severity_from_cc(cc),
                    category="complexity",
                    cwe="",
                )
                details.append(detail)
                flagged_functions.append(func)

        # Step 5: Calculate score
        score = 100.0
        score -= high_cc_count * 5
        score -= critical_cc_count * 10
        score -= deep_nesting_count * 2
        score -= many_params_count * 1
        score = max(0, min(100, score))

        # Step 6: Compute metrics
        avg_cc = sum(f["cyclomatic_complexity"] for f in all_functions) / len(
            all_functions
        )
        max_cc = max(f["cyclomatic_complexity"] for f in all_functions)
        cc_list = sorted(
            [f["cyclomatic_complexity"] for f in all_functions]
        )
        median_cc = cc_list[len(cc_list) // 2]

        avg_nesting = sum(
            f["max_nesting_depth"] for f in all_functions
        ) / len(all_functions)
        avg_params = sum(f["parameter_count"] for f in all_functions) / len(
            all_functions
        )
        avg_loc = sum(f["length"] for f in all_functions) / len(all_functions)

        metrics = {
            "tool_available": True,
            "files_analyzed": len(cpp_files),
            "functions_analyzed": len(all_functions),
            "avg_cyclomatic_complexity": round(avg_cc, 2),
            "max_cyclomatic_complexity": int(max_cc),
            "median_cyclomatic_complexity": int(median_cc),
            "high_cc_count": high_cc_count,
            "critical_cc_count": critical_cc_count,
            "deep_nesting_count": deep_nesting_count,
            "many_params_count": many_params_count,
            "avg_nesting_depth": round(avg_nesting, 2),
            "avg_parameters": round(avg_params, 2),
            "avg_lines_of_code": round(avg_loc, 2),
        }

        # Step 7: Build issues list
        issues = []
        if critical_cc_count > 0:
            issues.append(
                f"Found {critical_cc_count} function(s) with critical complexity (CC > 25)"
            )
        if high_cc_count > 0:
            issues.append(
                f"Found {high_cc_count} function(s) with high complexity (CC > 15)"
            )
        if deep_nesting_count > 0:
            issues.append(
                f"Found {deep_nesting_count} function(s) with deep nesting (depth > 4)"
            )
        if many_params_count > 0:
            issues.append(
                f"Found {many_params_count} function(s) with many parameters (> 6)"
            )
        if not issues:
            issues = ["All functions within acceptable complexity thresholds"]

        grade = self._score_to_grade(score)

        return {
            "score": score,
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
            "details": details,
            "tool_available": True,
        }

    @staticmethod
    def _severity_from_cc(cc: int) -> str:
        """Map cyclomatic complexity to severity."""
        if cc > 25:
            return "critical"
        elif cc > 15:
            return "high"
        elif cc > 10:
            return "medium"
        else:
            return "low"
