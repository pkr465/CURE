"""
C/C++ code complexity analysis (enhanced Version-3)
"""

import os
import re
from typing import Dict, List, Any
from statistics import median


class ComplexityAnalyzer:
    """
    Analyzes C/C++ code complexity using multiple metrics:
    - Cyclomatic complexity (CC)
    - Cognitive complexity (approximation with nesting penalties)
    - Nesting depth
    - Function length (LOC)
    - Parameter count
    - Boolean expression density (&&, ||, ternary ?)
    - Statement count
    - Recursion detection

    Produces:
    - Function-level metrics
    - File-level rollups
    - Hotspot/top-complex functions
    - Overall complexity score and grade
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".c++"}
    H_EXTS = {".h", ".hpp", ".hh", ".hxx", ".h++"}

    def __init__(self, codebase_path: str = None, project_root: str = None):
        """Initialize complexity analyzer."""
        self.codebase_path = codebase_path or os.getcwd()
        self.project_root = project_root or os.getcwd()
        self._file_cache: List[Dict[str, Any]] = []

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze C/C++ code complexity.

        Args:
            file_cache: List of processed C/C++ file entries with at least:
                {
                    "suffix": ".c" / ".h" / etc,
                    "source": "<file contents>",
                    "rel_path" or "path" or "file_relative_path" or "file_name"
                }

        Returns:
            Complexity analysis results with score, grade, issues, and detailed metrics.
        """
        self._file_cache = file_cache or []
        return self._calculate_complexity_score()

    def _calculate_complexity_score(self) -> Dict[str, Any]:
        """
        Calculate C/C++ complexity metrics and score.

        C/C++-only complexity analysis (no Python AST).
        Function-level parsing with brace-matching to extract bodies safely (skips strings/comments).

        Metrics per function:
        - Cyclomatic complexity (CC) based on decision points
        - Cognitive complexity (approximation with nesting penalties)
        - Max nesting depth
        - Lines of code (LOC) excluding comments/blank lines
        - Statement count (semicolon count)
        - Boolean expression density (&&, ||, ternary ?)
        - Case/default counts per switch
        - Parameter count
        - Recursion detection

        File-level rollups and hotspots:
        - Average/max CC per file
        - Top complex functions across codebase with relative paths and line numbers

        Scoring and issues:
        - Penalizes high average CC, very high max CC, deep nesting,
          long/parameter-heavy functions, boolean-heavy logic, and extremely high cognitive complexity.
        """
        if not self._file_cache:
            return {"score": 0, "grade": "F", "issues": ["No files cached"]}

        # Helper function to get relative path
        def _rel_path(entry: Dict[str, Any]) -> str:
            p = (
                entry.get("rel_path")
                or entry.get("path")
                or entry.get("file_relative_path")
                or entry.get("file_name")
                or ""
            )
            root = (
                getattr(self, "project_root", None)
                or getattr(self, "root_dir", None)
                or str(self.codebase_path)
            )
            try:
                return os.path.relpath(p, root) if p else ""
            except Exception:
                return p or ""

        # Get C/C++ files only
        c_cpp_files: List[Dict[str, Any]] = []
        all_exts = {ext.lower() for ext in (self.C_EXTS | self.H_EXTS)}
        for f in self._file_cache:
            suffix = (f.get("suffix") or "").lower()
            if suffix in all_exts:
                c_cpp_files.append(f)

        print(f"DEBUG complexity: Found {len(c_cpp_files)} C/C++ files out of {len(self._file_cache)} total")

        if not c_cpp_files:
            return {
                "score": 50,
                "grade": "C",
                "issues": [
                    f"No C/C++ files found for complexity analysis. "
                    f"Extensions checked: {sorted(self.C_EXTS | self.H_EXTS)}"
                ],
                "metrics": {"files_analyzed": 0, "total_functions": 0},
            }

        # Configurable thresholds
        THRESHOLDS = {
            "avg_cc_good": 5,
            "avg_cc_warn": 10,
            "max_cc_warn": 15,
            "max_cc_crit": 25,
            "cognitive_warn": 10,
            "cognitive_crit": 20,
            "nesting_warn": 4,
            "nesting_crit": 6,
            "loc_warn": 150,
            "loc_crit": 300,
            "params_warn": 6,
            "params_crit": 10,
            "bool_ops_warn": 8,
            "cases_warn": 10,
        }

        CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch"}

        # Simplified function detection regex
        sig_re = re.compile(
            r"""
            (?P<signature>
                (?:^[ \t]*(?:template\s*<[^>]*>\s*)*)?     # optional template
                [^\n;{}()]*?                               # qualifiers/return type
                (?P<name>[A-Za-z_~][\w:]*)
                \s*\(
                    (?P<params>[^;{}()]*)                  # parameters (no nested parens)
                \)
                (?:\s*const)?(?:\s*noexcept)?(?:\s*->\s*[^({]+)?   # qualifiers/trailing return
            )
            \s*\{                                          # function body start
        """,
            re.VERBOSE | re.MULTILINE | re.DOTALL,
        )

        def _find_matching_brace(src: str, start_idx: int) -> int:
            """Find matching closing brace, handling comments and strings."""
            depth = 1
            i = start_idx
            n = len(src)
            in_sl_comment = in_ml_comment = in_str = in_chr = False
            escape = False

            while i < n and depth > 0:
                ch = src[i]
                nxt = src[i + 1] if i + 1 < n else ""

                if in_sl_comment:
                    if ch == "\n":
                        in_sl_comment = False
                    i += 1
                    continue

                if in_ml_comment:
                    if ch == "*" and nxt == "/":
                        in_ml_comment = False
                        i += 2
                    else:
                        i += 1
                    continue

                if in_str:
                    if not escape and ch == '"':
                        in_str = False
                    escape = (ch == "\\" and not escape)
                    i += 1
                    continue

                if in_chr:
                    if not escape and ch == "'":
                        in_chr = False
                    escape = (ch == "\\" and not escape)
                    i += 1
                    continue

                # Not in any literal/comment
                if ch == "/" and nxt == "/":
                    in_sl_comment = True
                    i += 2
                    continue

                if ch == "/" and nxt == "*":
                    in_ml_comment = True
                    i += 2
                    continue

                if ch == '"':
                    in_str = True
                    i += 1
                    continue

                if ch == "'":
                    in_chr = True
                    i += 1
                    continue

                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1

                i += 1

            return i if depth == 0 else n - 1

        def _strip_comments_and_strings(src: str) -> str:
            """Return src without comments and string/char literals (preserve newlines)."""
            out: List[str] = []
            i = 0
            n = len(src)
            in_sl_comment = in_ml_comment = in_str = in_chr = False
            escape = False

            while i < n:
                ch = src[i]
                nxt = src[i + 1] if i + 1 < n else ""

                if in_sl_comment:
                    if ch == "\n":
                        in_sl_comment = False
                        out.append("\n")
                    i += 1
                    continue

                if in_ml_comment:
                    if ch == "*" and nxt == "/":
                        in_ml_comment = False
                        i += 2
                    else:
                        if ch == "\n":
                            out.append("\n")
                        i += 1
                    continue

                if in_str:
                    if not escape and ch == '"':
                        in_str = False
                    escape = (ch == "\\" and not escape)
                    if ch == "\n":
                        out.append("\n")
                    else:
                        out.append(" ")
                    i += 1
                    continue

                if in_chr:
                    if not escape and ch == "'":
                        in_chr = False
                    escape = (ch == "\\" and not escape)
                    if ch == "\n":
                        out.append("\n")
                    else:
                        out.append(" ")
                    i += 1
                    continue

                if ch == "/" and nxt == "/":
                    in_sl_comment = True
                    i += 2
                    continue

                if ch == "/" and nxt == "*":
                    in_ml_comment = True
                    i += 2
                    continue

                if ch == '"':
                    in_str = True
                    out.append('"')
                    i += 1
                    continue

                if ch == "'":
                    in_chr = True
                    out.append("'")
                    i += 1
                    continue

                out.append(ch)
                i += 1

            return "".join(out)

        def _count_params(params: str) -> int:
            """Count function parameters (best-effort, handles templates and nested parens)."""
            txt = params.strip()
            if not txt or txt == "void":
                return 0

            depth_angle = depth_paren = 0
            count = 1
            i = 0

            while i < len(txt):
                ch = txt[i]
                if ch == "<":
                    depth_angle += 1
                elif ch == ">":
                    depth_angle = max(0, depth_angle - 1)
                elif ch == "(":
                    depth_paren += 1
                elif ch == ")":
                    depth_paren = max(0, depth_paren - 1)
                elif ch == "," and depth_angle == 0 and depth_paren == 0:
                    count += 1
                i += 1

            return count

        def _compute_function_metrics(name: str, body: str) -> Dict[str, Any]:
            """Compute complexity metrics for a function body."""
            clean = _strip_comments_and_strings(body)

            # Base counts for cyclomatic complexity
            def count(pattern: str) -> int:
                return len(re.findall(pattern, clean))

            # Decision points
            if_count = count(r"\bif\b")
            for_count = count(r"\bfor\b")
            while_count = count(r"\bwhile\b")
            do_count = count(r"\bdo\b")
            case_count = count(r"\bcase\b")
            default_count = count(r"\bdefault\b")
            catch_count = count(r"\bcatch\b")
            ternary_count = clean.count("?")
            bool_ops = len(re.findall(r"&&|\|\|", clean))

            decision_points = (
                if_count
                + for_count
                + while_count
                + do_count
                + case_count
                + default_count
                + catch_count
                + ternary_count
                + bool_ops
            )
            cc = 1 + decision_points

            # Nesting and cognitive complexity approximation
            depth = 0
            max_depth = 0
            cognitive = 0

            for line in clean.splitlines():
                line_decisions = len(
                    re.findall(
                        r"\b(if|for|while|case|default|catch)\b|\?|\&\&|\|\|", line
                    )
                )
                cognitive += line_decisions + (line_decisions * max(0, depth))

                opens = line.count("{")
                closes = line.count("}")
                depth += opens
                depth -= closes
                if depth < 0:
                    depth = 0
                if depth > max_depth:
                    max_depth = depth

            # LOC (excluding comments/blank)
            loc = sum(1 for ln in clean.splitlines() if ln.strip())
            stmt_count = clean.count(";")

            # Recursion heuristic: call to same name within body
            recursion = re.search(r"\b" + re.escape(name) + r"\s*\(", clean) is not None

            bool_density = (bool_ops + ternary_count) / loc if loc > 0 else 0.0

            return {
                "cc": cc,
                "cognitive": cognitive,
                "max_nesting": max(0, max_depth - 1),  # subtract 1 for function's own block
                "loc": loc,
                "statements": stmt_count,
                "bool_ops": bool_ops,
                "ternary": ternary_count,
                "cases": case_count + default_count,
                "recursion": recursion,
                "bool_density": bool_density,
            }

        # Collect metrics
        all_functions: List[Dict[str, Any]] = []
        file_rollups: Dict[str, Dict[str, Any]] = {}

        print("DEBUG complexity: Starting function analysis...")

        for entry in c_cpp_files:
            source = entry.get("source", "") or ""
            if not source.strip():
                continue

            rel = _rel_path(entry)
            print(f"DEBUG complexity: Analyzing file {rel} ({len(source)} chars)")
            file_function_count = 0

            for m in sig_re.finditer(source):
                name = m.group("name")
                if name in CONTROL_KEYWORDS:
                    continue  # skip control structures

                params = m.group("params") or ""
                param_count = _count_params(params)

                # Determine body span
                body_start = m.end()  # first char after '{'
                end_idx = _find_matching_brace(source, body_start)
                body = source[body_start:end_idx]

                # Starting line number
                start_line = source.count("\n", 0, m.start()) + 1

                fm = _compute_function_metrics(name, body)
                fm.update(
                    {
                        "function": name,
                        "file": rel,
                        "start_line": start_line,
                        "params": param_count,
                    }
                )

                all_functions.append(fm)
                file_function_count += 1

            print(f"DEBUG complexity: Found {file_function_count} functions in {rel}")

            if rel not in file_rollups:
                file_rollups[rel] = {
                    "functions": 0,
                    "sum_cc": 0,
                    "max_cc": 0,
                    "avg_cc": 0.0,
                }

        print(f"DEBUG complexity: Total functions found: {len(all_functions)}")

        # Aggregate per-file
        by_file: Dict[str, List[Dict[str, Any]]] = {}
        for f in all_functions:
            by_file.setdefault(f["file"], []).append(f)

        for rel, funcs in by_file.items():
            scc = sum(x["cc"] for x in funcs)
            file_rollups[rel]["functions"] = len(funcs)
            file_rollups[rel]["sum_cc"] = scc
            file_rollups[rel]["max_cc"] = max(x["cc"] for x in funcs) if funcs else 0
            file_rollups[rel]["avg_cc"] = (scc / len(funcs)) if funcs else 0.0

        # Global metrics and scoring
        if not all_functions:
            return {
                "score": 50,
                "grade": "C",
                "issues": ["No C/C++ functions found"],
                "metrics": {
                    "files_analyzed": len(c_cpp_files),
                    "total_functions": 0,
                    "summary": {
                        "total_functions": 0,
                        "files_analyzed": len(c_cpp_files),
                    },
                },
            }

        cc_values = [f["cc"] for f in all_functions]
        cog_values = [f["cognitive"] for f in all_functions]
        nest_values = [f["max_nesting"] for f in all_functions]
        loc_values = [f["loc"] for f in all_functions]
        param_values = [f["params"] for f in all_functions]
        bool_ops_values = [f["bool_ops"] for f in all_functions]
        cases_values = [f["cases"] for f in all_functions]

        avg_cc = sum(cc_values) / len(cc_values)
        med_cc = median(cc_values)
        p90_cc = sorted(cc_values)[int(0.9 * (len(cc_values) - 1))]
        max_cc = max(cc_values)

        avg_cog = sum(cog_values) / len(cog_values) if cog_values else 0.0
        max_cog = max(cog_values) if cog_values else 0.0
        max_nesting = max(nest_values) if nest_values else 0

        long_functions = sum(1 for l in loc_values if l >= THRESHOLDS["loc_warn"])
        very_long_functions = sum(1 for l in loc_values if l >= THRESHOLDS["loc_crit"])
        deep_nesting_funcs = sum(
            1 for n in nest_values if n >= THRESHOLDS["nesting_warn"]
        )
        heavy_param_funcs = sum(
            1 for p in param_values if p >= THRESHOLDS["params_warn"]
        )
        very_heavy_param_funcs = sum(
            1 for p in param_values if p >= THRESHOLDS["params_crit"]
        )
        bool_heavy_funcs = sum(
            1 for b in bool_ops_values if b >= THRESHOLDS["bool_ops_warn"]
        )
        many_cases_funcs = sum(
            1 for c in cases_values if c >= THRESHOLDS["cases_warn"]
        )

        # Hotspots: top 10 functions by CC, then cognitive, then LOC
        top_funcs = sorted(
            all_functions,
            key=lambda x: (x["cc"], x["cognitive"], x["loc"]),
            reverse=True,
        )[:10]
        top_files = sorted(
            file_rollups.items(),
            key=lambda x: (x[1]["avg_cc"], x[1]["max_cc"]),
            reverse=True,
        )[:10]

        # Score
        score = 100
        issues: List[str] = []

        # Average CC penalties
        if avg_cc > THRESHOLDS["avg_cc_good"]:
            over = avg_cc - THRESHOLDS["avg_cc_good"]
            score -= min(30, int(round(over * 2)))  # up to -30
            issues.append(
                f"Average cyclomatic complexity is {avg_cc:.2f} "
                f"(above {THRESHOLDS['avg_cc_good']})"
            )

        # Max CC penalties (+ count of extreme offenders)
        extreme_cc_funcs = sum(1 for v in cc_values if v >= THRESHOLDS["max_cc_crit"])
        high_cc_funcs = sum(
            1
            for v in cc_values
            if THRESHOLDS["max_cc_warn"] <= v < THRESHOLDS["max_cc_crit"]
        )

        if max_cc > THRESHOLDS["max_cc_crit"]:
            score -= min(25, (max_cc - THRESHOLDS["max_cc_crit"]))
            issues.append(
                f"Very high complexity function detected (CC: {max_cc}, "
                f"{extreme_cc_funcs} function(s) with CC >= {THRESHOLDS['max_cc_crit']})"
            )
        elif max_cc > THRESHOLDS["max_cc_warn"]:
            issues.append(
                f"High complexity functions detected (max CC: {max_cc}, "
                f"{high_cc_funcs} function(s) with CC >= {THRESHOLDS['max_cc_warn']})"
            )

        # Cognitive complexity penalties
        if avg_cog > THRESHOLDS["cognitive_warn"]:
            score -= min(
                15, int(round((avg_cog - THRESHOLDS["cognitive_warn"]) * 0.8))
            )
            issues.append(
                f"Average cognitive complexity is {avg_cog:.2f} "
                f"(above {THRESHOLDS['cognitive_warn']})"
            )
        if max_cog > THRESHOLDS["cognitive_crit"]:
            score -= 10
            issues.append(
                f"Extremely high cognitive complexity detected "
                f"(max cognitive: {max_cog:.2f} > {THRESHOLDS['cognitive_crit']})"
            )

        # Nesting penalties
        if max_nesting >= THRESHOLDS["nesting_crit"]:
            score -= 10
            issues.append(
                f"Very deep nesting detected (max nesting: {max_nesting}, "
                f"{deep_nesting_funcs} function(s) with nesting >= {THRESHOLDS['nesting_warn']})"
            )
        elif max_nesting >= THRESHOLDS["nesting_warn"]:
            issues.append(
                f"Deep nesting detected (max nesting: {max_nesting}, "
                f"{deep_nesting_funcs} function(s) with nesting >= {THRESHOLDS['nesting_warn']})"
            )

        # Long functions
        if long_functions:
            score -= min(10, long_functions)  # -1 per long function up to 10
            issues.append(
                f"{long_functions} long function(s) "
                f"(>= {THRESHOLDS['loc_warn']} LOC)"
            )
        if very_long_functions:
            score -= min(10, very_long_functions)
            issues.append(
                f"{very_long_functions} very long function(s) "
                f"(>= {THRESHOLDS['loc_crit']} LOC)"
            )

        # Heavy parameter lists
        if heavy_param_funcs:
            score -= min(8, heavy_param_funcs)
            issues.append(
                f"{heavy_param_funcs} function(s) with many parameters "
                f"(>= {THRESHOLDS['params_warn']})"
            )
        if very_heavy_param_funcs:
            score -= min(8, very_heavy_param_funcs)
            issues.append(
                f"{very_heavy_param_funcs} function(s) with extremely many parameters "
                f"(>= {THRESHOLDS['params_crit']})"
            )

        # Boolean-heavy logic
        if bool_heavy_funcs:
            score -= min(6, bool_heavy_funcs)
            issues.append(
                f"{bool_heavy_funcs} function(s) with heavy boolean logic "
                f"(>= {THRESHOLDS['bool_ops_warn']} boolean ops)"
            )

        # Many-case switches
        if many_cases_funcs:
            # Light penalty – many cases often indicate big switches
            score -= min(5, many_cases_funcs)
            issues.append(
                f"{many_cases_funcs} function(s) with many switch cases "
                f"(>= {THRESHOLDS['cases_warn']} cases)"
            )

        # Add positive feedback if no issues
        if not issues:
            issues.append("Good complexity metrics - well-structured code!")

        score = max(0, min(100, score))
        grade = self._score_to_grade(score)

        print(
            f"DEBUG complexity: Final score: {score}, functions: {len(all_functions)}, "
            f"avg CC: {avg_cc:.2f}"
        )

        # Build metrics payload
        metrics = {
            "summary": {
                "total_functions": len(all_functions),
                "files_analyzed": len(by_file),
                "average_cc": round(avg_cc, 2),
                "median_cc": round(med_cc, 2),
                "p90_cc": p90_cc,
                "max_cc": max_cc,
                "average_cognitive": round(avg_cog, 2),
                "max_cognitive": round(max_cog, 2),
                "max_nesting": max_nesting,
                "long_functions": long_functions,
                "very_long_functions": very_long_functions,
                "deep_nesting_functions": deep_nesting_funcs,
                "heavy_param_functions": heavy_param_funcs,
                "very_heavy_param_functions": very_heavy_param_funcs,
                "boolean_heavy_functions": bool_heavy_funcs,
                "many_cases_functions": many_cases_funcs,
            },
            "top_complex_functions": [
                {
                    "function": f["function"],
                    "file": f["file"],
                    "start_line": f["start_line"],
                    "cc": f["cc"],
                    "cognitive": f["cognitive"],
                    "max_nesting": f["max_nesting"],
                    "loc": f["loc"],
                    "params": f["params"],
                    "bool_ops": f["bool_ops"],
                    "bool_density": round(f["bool_density"], 3),
                    "cases": f["cases"],
                    "statements": f["statements"],
                    "recursion": f["recursion"],
                }
                for f in top_funcs
            ],
            "files": [
                {
                    "file": rel,
                    "functions": data["functions"],
                    "avg_cc": round(data["avg_cc"], 2),
                    "max_cc": data["max_cc"],
                    "sum_cc": data["sum_cc"],
                }
                for rel, data in top_files
            ],
        }

        return {
            "score": round(score, 1),
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
        }

    @staticmethod
    def _score_to_grade(score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"