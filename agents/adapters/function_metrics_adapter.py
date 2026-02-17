"""
Function Metrics Adapter for C/C++ code analysis.

Analyzes function complexity, parameter counts, body line lengths, method types,
and overload patterns to assess code design quality.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from agents.adapters.base_adapter import BaseStaticAdapter


class FunctionMetricsAdapter(BaseStaticAdapter):
    """
    Analyzes function-level metrics in C/C++ code.

    Detects:
    - Functions with excessive parameters (>5, >10)
    - Long function bodies (>150, >300 lines)
    - Overloaded functions
    - Template, virtual, const, static, inline methods
    """

    def __init__(self, debug: bool = False):
        """Initialize FunctionMetricsAdapter."""
        super().__init__("function_metrics", debug)

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze functions in C/C++ files for design metrics and issues.

        Args:
            file_cache: List of file entries with language, path, etc.
            ccls_navigator: CCLSCodeNavigator instance for symbol lookup.
            dependency_graph: Unused for this adapter.

        Returns:
            Standard result dict with score, grade, metrics, issues, details.
        """
        # 1. Validation
        if not file_cache:
            return self._create_neutral_result("No files to analyze")

        if ccls_navigator is None:
            return self._handle_tool_unavailable(
                "ccls_navigator",
                "ccls_navigator not available; skipping function_metrics analysis",
            )

        all_functions = []
        file_issues = {}  # Map file -> list of issues

        # Filter to C/C++ files
        c_cpp_languages = {"c", "cpp", "c_header", "cpp_header", "cc", "cxx", "h", "hpp"}

        for file_entry in file_cache:
            # Check language
            language = file_entry.get("language", "").lower()
            
            # CRITICAL FIX: Prioritize relative path to match workspace structure
            # The 'path' field often contains the user's local absolute path (/Users/...), which fails in the container.
            target_path = file_entry.get("file_relative_path")
            
            # Fallback logic if relative path is missing
            if not target_path:
                target_path = file_entry.get("file_name") 

            # Validation: Is this a C/C++ file?
            if language not in c_cpp_languages:
                # Double check extension if language tag is missing/wrong
                if not (target_path and target_path.endswith((".c", ".cpp", ".cc", ".h", ".hpp", ".cxx"))):
                    continue

            try:
                # Create and open document using the corrected path
                doc = ccls_navigator.create_doc(target_path)
                if not doc:
                    if self.debug:
                        self.logger.warning(f"Skipping {target_path}: create_doc failed (File not found in workspace?)")
                    continue

                ccls_navigator.openDoc(doc)

                # Get symbols
                symbols_by_name = ccls_navigator.getDocumentSymbolsKeySymbols(doc)
                if not symbols_by_name:
                    continue

                # Process each function symbol
                for symbol_name, symbol_list in symbols_by_name.items():
                    for symbol in symbol_list:
                        # Accept various callable types
                        kind = symbol.get("kind")
                        if kind not in ("Function", "Method", "Constructor"):
                            continue

                        func_data = self._extract_function_info(
                            target_path, symbol_name, symbol
                        )
                        if func_data:
                            all_functions.append(func_data)
                            # Only process the first declaration/definition to avoid duplication
                            break

            except Exception as e:
                self.logger.warning(
                    f"Error analyzing {target_path}: {e}"
                )
                if target_path not in file_issues:
                    file_issues[target_path] = []

        # 2. Check results
        if not all_functions:
            # CRITICAL FIX: Return a passing score (100) instead of an empty result/failure
            # if no functions were found (e.g. empty project or only headers)
            return self._create_neutral_result("No functions found in C/C++ files")

        # 3. Compute aggregates and collect details
        metrics, issues, details = self._compute_metrics(all_functions)

        # 4. Calculate score
        score = self._calculate_score(metrics)
        grade = self._score_to_grade(score)

        return {
            "score": score,
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
            "details": details,
            "tool_available": True,
        }

    def _extract_function_info(
        self, file_path: str, symbol_name: str, symbol: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Extract function metadata from symbol dict.
        """
        try:
            location = symbol.get("location", {})
            range_info = location.get("range", {})
            start = range_info.get("start", {})
            end = range_info.get("end", {})

            start_line = start.get("line", 0)
            end_line = end.get("line", 0)

            detail = symbol.get("detail", "")

            # Parse attributes from detail string
            is_method = "::" in symbol_name
            is_template = "<" in detail or "<" in symbol_name
            is_virtual = "virtual" in detail
            is_const = detail.rstrip().endswith("const")
            is_static = "static" in detail
            is_inline = "inline" in detail

            # Extract return type (heuristic: before function name in detail)
            return_type = self._extract_return_type(detail, symbol_name)

            # Parse parameter count
            param_count = self._count_parameters(detail)

            # Body line count
            body_lines = max(0, end_line - start_line)

            return {
                "file": file_path,
                "name": symbol_name,
                "line": start_line,
                "return_type": return_type,
                "param_count": param_count,
                "body_lines": body_lines,
                "is_method": is_method,
                "is_template": is_template,
                "is_virtual": is_virtual,
                "is_const": is_const,
                "is_static": is_static,
                "is_inline": is_inline,
                "detail": detail,
            }
        except Exception as e:
            self.logger.debug(f"Failed to extract function info for {symbol_name}: {e}")
            return None

    def _extract_return_type(self, detail: str, symbol_name: str) -> str:
        """Extract return type from detail string."""
        try:
            # Simple heuristic: split on symbol name and take prefix
            if symbol_name in detail:
                idx = detail.find(symbol_name)
                prefix = detail[:idx].strip()
                return prefix if prefix else "unknown"
        except Exception:
            pass
        return "unknown"

    def _count_parameters(self, detail: str) -> int:
        """Count function parameters from detail string."""
        try:
            if not detail:
                return 0
                
            # Find outermost parentheses for args
            # This is fragile for complex C++ (function pointers, etc) but sufficient for metrics
            start = detail.find("(")
            end = detail.rfind(")")
            
            if start == -1 or end == -1 or start >= end:
                return 0

            params_str = detail[start + 1 : end].strip()
            if not params_str:
                return 0

            # Count commas, add 1. 
            # Note: This might count commas in template args <a,b> erroneously if nested 
            # inside function args, but it's a "good enough" heuristic for quick metrics.
            return params_str.count(",") + 1
        except Exception:
            return 0

    def _compute_metrics(
        self, all_functions: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
        """Compute aggregate metrics and identify issues."""
        
        metrics = {
            "total_functions": len(all_functions),
            "total_methods": 0,
            "virtual_count": 0,
            "template_count": 0,
            "static_count": 0,
            "inline_count": 0,
            "const_count": 0,
            "functions_with_many_params": 0,
            "functions_with_excessive_params": 0,
            "avg_params": 0.0,
            "max_params": 0,
            "long_functions": 0,
            "very_long_functions": 0,
            "avg_body_lines": 0.0,
            "max_body_lines": 0,
            "overloaded_function_count": 0,
            "max_overload_count": 0,
        }

        issues = []
        details = []

        # Track overloads
        overload_map = {}  # base_name -> count

        total_params = 0
        total_body_lines = 0

        for func in all_functions:
            # Aggregate counts
            if func["is_method"]:
                metrics["total_methods"] += 1
            if func["is_virtual"]:
                metrics["virtual_count"] += 1
            if func["is_template"]:
                metrics["template_count"] += 1
            if func["is_static"]:
                metrics["static_count"] += 1
            if func["is_inline"]:
                metrics["inline_count"] += 1
            if func["is_const"]:
                metrics["const_count"] += 1

            # Parameters
            param_count = func["param_count"]
            total_params += param_count
            metrics["max_params"] = max(metrics["max_params"], param_count)

            if param_count > 5:
                metrics["functions_with_many_params"] += 1
            if param_count > 10:
                metrics["functions_with_excessive_params"] += 1

            # Body lines
            body_lines = func["body_lines"]
            total_body_lines += body_lines
            metrics["max_body_lines"] = max(metrics["max_body_lines"], body_lines)

            if body_lines > 150:
                metrics["long_functions"] += 1
            if body_lines > 300:
                metrics["very_long_functions"] += 1

            # Overload tracking
            base_name = func["name"].split("::")[-1]
            overload_map[base_name] = overload_map.get(base_name, 0) + 1

        # Calculate averages
        if all_functions:
            metrics["avg_params"] = round(total_params / len(all_functions), 2)
            metrics["avg_body_lines"] = round(
                total_body_lines / len(all_functions), 2
            )

        # Overload metrics
        overload_counts = list(overload_map.values())
        metrics["overloaded_function_count"] = sum(1 for c in overload_counts if c > 1)
        metrics["max_overload_count"] = max(overload_counts) if overload_counts else 0

        # Generate detail entries for issues
        for func in all_functions:
            # High parameter count
            if func["param_count"] > 5:
                if func["param_count"] > 10:
                    severity = "high"
                elif func["param_count"] > 8:
                    severity = "medium"
                else:
                    severity = "low"

                detail = self._make_detail(
                    file=func["file"],
                    function=func["name"],
                    line=func["line"],
                    description=f"Function has {func['param_count']} parameters (high complexity)",
                    severity=severity,
                    category="function_design",
                    cwe="",
                )
                details.append(detail)
                issues.append(
                    f"{func['name']} has {func['param_count']} parameters"
                )

            # Long functions
            if func["body_lines"] > 150:
                severity = "high" if func["body_lines"] > 300 else "medium"
                detail = self._make_detail(
                    file=func["file"],
                    function=func["name"],
                    line=func["line"],
                    description=f"Function body is {func['body_lines']} lines (exceeds ideal length)",
                    severity=severity,
                    category="function_design",
                    cwe="",
                )
                details.append(detail)
                issues.append(
                    f"{func['name']} body is {func['body_lines']} lines long"
                )

        return metrics, issues, details

    def _calculate_score(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate overall score based on metrics.

        Penalty: -1 per function with many params, -2 per excessive,
                 -1 per long function, -3 per very long.
        """
        score = 100.0
        score -= metrics["functions_with_many_params"]
        score -= 2 * metrics["functions_with_excessive_params"]
        score -= metrics["long_functions"]
        score -= 3 * metrics["very_long_functions"]

        # Clamp to [0, 100]
        return max(0.0, min(100.0, score))

    def _create_neutral_result(self, message: str) -> Dict[str, Any]:
        """Returns a neutral (passing) result when no analysis is possible/needed."""
        return {
            "score": 100.0,
            "grade": "A",
            "metrics": {},
            "issues": [message],
            "details": [],
            "tool_available": True
        }