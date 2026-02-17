import json
from pathlib import Path
from html import escape
from typing import Any, Dict, Optional, List
from utils.parsers.env_parser import EnvConfig


class HealthReportHTMLGenerator:
    """
    HealthReportHTMLGenerator

    Usage (class-only):
        gen = HealthReportHTMLGenerator.from_json_file("healthreport.json")
        gen.save_html("some/output/dir/healthreport.html")

    See run_health_report() below for a simple two-parameter wrapper.
    """

    def __init__(self, report: Dict[str, Any]) -> None:
        self.report = report or {}

    @classmethod
    def from_json_file(cls, path: str) -> "HealthReportHTMLGenerator":
        p = Path(path)
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data)

    # ----------------- Internal helpers -----------------

    def _get_health_metrics(self) -> Dict[str, Any]:
        return self.report.get("health_metrics", {}) or {}

    def _get_overall_scores(self) -> Dict[str, tuple]:
        hm = self._get_health_metrics()
        overall = hm.get("overall_health", {}) or {}

        # Define the sub-metrics to check
        metric_keys = [
            "dependency_score",
            "quality_score",
            "complexity_score",
            "maintainability_score",
            "documentation_score",
            "test_coverage_score",
            "security_score",
            "runtime_risk_score",  # Added: Runtime Risk Score
        ]

        # 1. Collect sub-metrics and identify failures
        sub_metric_data = {}
        failing_kpis = []

        for key in metric_keys:
            metric = hm.get(key, {}) or {}
            score = metric.get("score")
            grade = metric.get("grade")

            # Format name: "dependency_score" -> "Dependency"
            # Special handling for "runtime_risk_score" -> "Runtime Risk"
            if key == "runtime_risk_score":
                name = "Runtime Risk"
            else:
                name = key.replace("_score", "").capitalize()

            # Store data for later
            sub_metric_data[key] = (name, score, grade)

            # Check for failure
            if grade == "F":
                failing_kpis.append(name)

        # 2. Build the result dictionary
        # Structure: "MetricName": (score, grade, failing_reasons_list)
        scores = {}

        # Handle Overall Score
        overall_grade = overall.get("grade")
        overall_failures = []
        # If overall is F, we attribute it to the specific sub-metrics that failed
        if overall_grade == "F":
            overall_failures = failing_kpis

        scores["Overall"] = (
            overall.get("score"),
            overall_grade,
            overall_failures,
        )

        # Add sub-metrics to the scores dict in the specific order
        for key in metric_keys:
            name, s, g = sub_metric_data[key]
            # Sub-metrics don't need a failure list for themselves in this context
            scores[name] = (s, g, [])

        return scores

    def _get_top_complex_functions(self, limit: int = 5) -> List[Dict[str, Any]]:
        metrics = (
            self._get_health_metrics()
            .get("complexity_score", {})
            .get("metrics", {})
            or {}
        )
        top = metrics.get("top_complex_functions", []) or []
        return top[:limit]

    def _get_maintainability_metrics(self) -> Dict[str, Any]:
        ms = (
            self._get_health_metrics()
            .get("maintainability_score", {})
            .get("metrics", {})
            or {}
        )
        # ms["files"] is a list of per-file metrics returned by MaintainabilityAnalyzer
        return {
            "avg_mi": ms.get("avg_mi"),
            "min_mi": ms.get("min_mi"),
            "max_mi": ms.get("max_mi"),
            "avg_complexity": ms.get("avg_complexity"),
            "avg_comment_ratio": ms.get("avg_comment_ratio"),
            "total_banned_apis": ms.get("total_banned_apis"),
            "total_future_banned_apis": ms.get("total_future_banned_apis"),
            "worst_mi_files": ms.get("worst_mi_files", []) or [],
            "header_guard_issues_files": ms.get("header_guard_issues_files", []) or [],
            "switch_missing_default_files": ms.get("switch_missing_default_files", [])
            or [],
            "files": ms.get("files", []) or [],
        }

    def _get_worst_files_by_mi(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Build a list of worst files by MI with details:
        file, maintainability_index, loc, cyclomatic_complexity, comment_ratio

        Using the structure returned by the updated MaintainabilityAnalyzer:
          metrics["files"] -> list of per-file dicts
          metrics["worst_mi_files"] -> list of file paths
        """
        mm = self._get_maintainability_metrics()
        files = mm.get("files", [])
        worst_list = mm.get("worst_mi_files", [])

        # Build a lookup by file path
        by_file = {f.get("file"): f for f in files if "file" in f}

        result: List[Dict[str, Any]] = []
        for fpath in worst_list:
            data = by_file.get(fpath)
            if not data:
                continue
            result.append(
                {
                    "file": fpath,
                    "maintainability_index": data.get("maintainability_index"),
                    # In the new analyzer, LOC is "code_lines"; we also have "total_lines"
                    "loc": data.get("code_lines", data.get("total_lines")),
                    "cyclomatic_complexity": data.get("cyclomatic_complexity"),
                    "comment_ratio": data.get("comment_ratio"),
                }
            )
            if len(result) >= limit:
                break

        return result

    def _get_dependency_metrics(self) -> Dict[str, Any]:
        ds = (
            self._get_health_metrics()
            .get("dependency_score", {})
            .get("metrics", {})
            or {}
        )
        return {
            "scc_count": ds.get("scc_count"),
            "largest_cycle_size": ds.get("largest_cycle_size"),
            "missing_includes": ds.get("missing_includes"),
            "max_fan_out": ds.get("max_fan_out"),
            "avg_fan_out": ds.get("avg_fan_out"),
            "header_to_source_ratio": ds.get("header_to_source_ratio"),
            "internal_nodes": ds.get("internal_nodes"),
            "external_nodes": ds.get("external_nodes"),
            "total_edges": ds.get("total_edges"),
        }

    def _get_dependency_issues(self) -> List[str]:
        dep = self._get_health_metrics().get("dependency_score", {}) or {}
        return dep.get("issues", []) or []

    def _get_maintainability_issues(self) -> List[str]:
        ms = self._get_health_metrics().get("maintainability_score", {}) or {}
        return ms.get("issues", []) or []

    def _get_security_violations(self) -> Dict[str, List[Dict[str, Any]]]:
        sec = self._get_health_metrics().get("security_score", {}) or {}
        metrics = sec.get("metrics", {}) or {}
        violations_by_file = metrics.get("violations_by_file", {}) or {}
        return violations_by_file

    def _get_runtime_risk_metrics(self) -> Dict[str, Any]:
        rr = (
            self._get_health_metrics()
            .get("runtime_risk_score", {})
            .get("metrics", {})
            or {}
        )
        return {
            "deadlock_issues": rr.get("deadlock_issues", 0),
            "memory_corruption_issues": rr.get("memory_corruption_issues", 0),
            "null_pointer_issues": rr.get("null_pointer_issues", 0),
            "total_issues": rr.get("total_issues", 0),
        }

    def _get_runtime_risk_issues(self) -> List[str]:
        rr = self._get_health_metrics().get("runtime_risk_score", {}) or {}
        return rr.get("issues", []) or []

    def _get_test_metrics(self) -> Dict[str, Any]:
        t = (
            self._get_health_metrics()
            .get("test_coverage_score", {})
            .get("metrics", {})
            or {}
        )
        return {
            "total_source_files": t.get("total_source_files"),
            "total_test_files": t.get("total_test_files"),
            "test_ratio": t.get("test_ratio"),
            "untested_sources": t.get("untested_sources", []) or [],
        }

    def _get_doc_metrics(self) -> Dict[str, Optional[float]]:
        d = (
            self._get_health_metrics()
            .get("documentation_score", {})
            .get("metrics", {})
            or {}
        )
        return {
            "documentation_ratio": d.get("documentation_ratio"),
            "header_documentation_ratio": d.get("header_documentation_ratio"),
            "function_impl_coverage_ratio": d.get("function_impl_coverage_ratio"),
            "type_coverage_ratio": d.get("type_coverage_ratio"),
            "macro_coverage_ratio": d.get("macro_coverage_ratio"),
        }

    @staticmethod
    def _grade_to_class(grade: Optional[str]) -> str:
        if not grade:
            return ""
        g = grade.upper()
        if g == "A":
            return "grade-A"
        if g in ("B", "C"):
            return "grade-B"
        if g == "D":
            return "grade-D"
        return "grade-F"

    @staticmethod
    def _ratio_to_percent(value: Optional[float]) -> str:
        try:
            if value is None:
                return ""
            return f"{value * 100:.1f}%"
        except Exception:
            return ""

    # ----------------- Public API -----------------

    def generate_html(self) -> str:
        """
        Generate a full HTML summary page in tabular format.
        """
        scores = self._get_overall_scores()
        top_funcs = self._get_top_complex_functions(limit=5)
        worst_files = self._get_worst_files_by_mi(limit=5)
        dep_metrics = self._get_dependency_metrics()
        dep_issues = self._get_dependency_issues()
        maint_metrics = self._get_maintainability_metrics()
        maint_issues = self._get_maintainability_issues()
        sec_violations = self._get_security_violations()
        runtime_metrics = self._get_runtime_risk_metrics()
        runtime_issues = self._get_runtime_risk_issues()
        test_metrics = self._get_test_metrics()
        doc_metrics = self._get_doc_metrics()

        # Update: Fetch codebase name from report, EnvConfig, or fallback
        codebase_name = self.report.get("codebase_name")
        if not codebase_name:
            codebase_name = self.report.get("health_metrics", {}).get("codebase_name")
        if not codebase_name:
             # Try fetching from EnvConfig if available as a fallback
             codebase_name = getattr(EnvConfig, "codebase_name", None)
        
        if not codebase_name:
            codebase_name = "Codebase"
            
        title = f"{codebase_name} Health Summary"

        html_parts: List[str] = []

        # HTML head
        html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{escape(title)}</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 16px; color: #222; }}
  h1, h2, h3 {{ margin-bottom: 0.4em; }}
  h1 {{ font-size: 1.8em; }}
  h2 {{ font-size: 1.4em; margin-top: 1.4em; }}
  h3 {{ font-size: 1.1em; margin-top: 1em; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 8px; font-size: 0.9em; }}
  th {{ background: #f0f0f0; text-align: left; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  code {{ font-family: Consolas, monospace; background: #f6f6f6; padding: 1px 4px; border-radius: 3px; }}
  .score-badge {{
    display: inline-block;
    padding: 1px 8px;
    border-radius: 10px;
    font-size: 0.8em;
    color: #fff;
  }}
  .grade-A {{ background: #2e7d32; }}
  .grade-B {{ background: #558b2f; }}
  .grade-D {{ background: #f9a825; }}
  .grade-F {{ background: #c62828; }}
  .section-note {{ font-size: 0.85em; color: #555; margin-bottom: 8px; }}
  .fail-reason {{ font-size: 0.85em; color: #c62828; margin-left: 8px; font-style: italic; }}
</style>
</head>
<body>
""")

        html_parts.append(f"<h1>{escape(title)}</h1>\n")

        # Overall scores table
        html_parts.append("<h2>Overall Scores</h2>\n")
        html_parts.append(
            "<table>\n<tr><th>Metric</th><th>Score</th><th>Grade</th></tr>\n"
        )
        for metric_name, (score, grade, failing_kpis) in scores.items():
            score_str = "" if score is None else f"{score:.1f}"
            grade_str = grade or ""
            css_class = self._grade_to_class(grade_str)

            # Construct display name with failing KPIs if present
            display_name = escape(metric_name)
            if failing_kpis:
                kpi_list_str = ", ".join(failing_kpis)
                display_name += (
                    f"<span class='fail-reason'>Failed by: {escape(kpi_list_str)}</span>"
                )

            html_parts.append(
                "<tr>"
                f"<td>{display_name}</td>"
                f"<td>{escape(score_str)}</td>"
                f"<td><span class='score-badge {css_class}'>{escape(grade_str)}</span></td>"
                "</tr>\n"
            )
        html_parts.append("</table>\n")

        # Dependency summary
        html_parts.append("<h2>Dependency Summary</h2>\n")
        if dep_metrics:
            html_parts.append("<table>\n<tr><th>Metric</th><th>Value</th></tr>\n")
            html_parts.append(
                f"<tr><td>Strongly Connected Components (cycles)</td>"
                f"<td>{escape(str(dep_metrics.get('scc_count')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Largest cycle size</td>"
                f"<td>{escape(str(dep_metrics.get('largest_cycle_size')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Missing includes</td>"
                f"<td>{escape(str(dep_metrics.get('missing_includes')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Max fan-out</td>"
                f"<td>{escape(str(dep_metrics.get('max_fan_out')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Average fan-out</td>"
                f"<td>{escape(str(dep_metrics.get('avg_fan_out')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Header-to-source ratio</td>"
                f"<td>{escape(str(dep_metrics.get('header_to_source_ratio')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Internal nodes</td>"
                f"<td>{escape(str(dep_metrics.get('internal_nodes')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>External nodes</td>"
                f"<td>{escape(str(dep_metrics.get('external_nodes')))}</td></tr>\n"
            )
            html_parts.append(
                f"<tr><td>Total edges</td>"
                f"<td>{escape(str(dep_metrics.get('total_edges')))}</td></tr>\n"
            )
            html_parts.append("</table>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No dependency metrics available.</p>\n"
            )

        # Dependency issues
        html_parts.append("<h3>Dependency Issues</h3>\n")
        if dep_issues:
            html_parts.append("<ul>\n")
            for issue in dep_issues[:20]:
                html_parts.append(f"<li>{escape(str(issue))}</li>\n")
            if len(dep_issues) > 20:
                html_parts.append(
                    f"<li class='section-note'>... {len(dep_issues) - 20} more</li>\n"
                )
            html_parts.append("</ul>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No dependency issues reported.</p>\n"
            )

        # Top complex functions
        html_parts.append("<h2>Top 5 Complex Functions</h2>\n")
        if top_funcs:
            html_parts.append("<table>\n")
            html_parts.append(
                "<tr><th>Function</th><th>File</th><th>Start Line</th>"
                "<th>Cyclomatic Complexity</th><th>Cognitive Complexity</th><th>Max Nesting</th></tr>\n"
            )
            for f in top_funcs:
                html_parts.append(
                    "<tr>"
                    f"<td>{escape(str(f.get('function')))}</td>"
                    f"<td><code>{escape(str(f.get('file')))}</code></td>"
                    f"<td>{escape(str(f.get('start_line')))}</td>"
                    f"<td>{escape(str(f.get('cc')))}</td>"
                    f"<td>{escape(str(f.get('cognitive')))}</td>"
                    f"<td>{escape(str(f.get('max_nesting')))}</td>"
                    "</tr>\n"
                )
            html_parts.append("</table>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No complexity data available.</p>\n"
            )

        # Maintainability summary
        html_parts.append("<h2>Maintainability Summary</h2>\n")
        html_parts.append("<table>\n<tr><th>Metric</th><th>Value</th></tr>\n")
        html_parts.append(
            f"<tr><td>Average Maintainability Index</td>"
            f"<td>{escape(str(maint_metrics.get('avg_mi')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Min Maintainability Index</td>"
            f"<td>{escape(str(maint_metrics.get('min_mi')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Max Maintainability Index</td>"
            f"<td>{escape(str(maint_metrics.get('max_mi')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Average complexity</td>"
            f"<td>{escape(str(maint_metrics.get('avg_complexity')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Average comment ratio</td>"
            f"<td>{escape(str(maint_metrics.get('avg_comment_ratio')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Total banned API usages</td>"
            f"<td>{escape(str(maint_metrics.get('total_banned_apis')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Total future-banned API usages</td>"
            f"<td>{escape(str(maint_metrics.get('total_future_banned_apis')))}</td></tr>\n"
        )
        html_parts.append("</table>\n")

        # Maintainability issues
        html_parts.append("<h3>Maintainability Issues</h3>\n")
        if maint_issues:
            html_parts.append("<ul>\n")
            for issue in maint_issues[:20]:
                html_parts.append(f"<li>{escape(str(issue))}</li>\n")
            if len(maint_issues) > 20:
                html_parts.append(
                    f"<li class='section-note'>... {len(maint_issues) - 20} more</li>\n"
                )
            html_parts.append("</ul>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No maintainability issues reported.</p>\n"
            )

        # Worst files by maintainability index
        html_parts.append("<h2>Top 5 Worst Files by Maintainability Index</h2>\n")
        if worst_files:
            html_parts.append("<table>\n")
            html_parts.append(
                "<tr><th>File</th><th>Maintainability Index</th>"
                "<th>LOC</th><th>Cyclomatic Complexity</th><th>Comment Ratio</th></tr>\n"
            )
            for wf in worst_files:
                comment_ratio = wf.get("comment_ratio")
                cr_str = "" if comment_ratio is None else f"{comment_ratio:.3f}"
                html_parts.append(
                    "<tr>"
                    f"<td><code>{escape(str(wf.get('file')))}</code></td>"
                    f"<td>{escape(str(wf.get('maintainability_index')))}</td>"
                    f"<td>{escape(str(wf.get('loc')))}</td>"
                    f"<td>{escape(str(wf.get('cyclomatic_complexity')))}</td>"
                    f"<td>{escape(cr_str)}</td>"
                    "</tr>\n"
                )
            html_parts.append("</table>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No maintainability data available.</p>\n"
            )

        # Security violations
        html_parts.append("<h2>Security Violations by File</h2>\n")
        if sec_violations:
            html_parts.append("<table>\n")
            html_parts.append(
                "<tr><th>File</th><th>Rule</th><th>Severity</th>"
                "<th>Description</th><th>Line</th><th>Snippet</th></tr>\n"
            )
            for file_path, issues in sec_violations.items():
                for v in issues:
                    html_parts.append(
                        "<tr>"
                        f"<td><code>{escape(file_path)}</code></td>"
                        f"<td>{escape(str(v.get('rule')))}</td>"
                        f"<td>{escape(str(v.get('severity')))}</td>"
                        f"<td>{escape(str(v.get('description')))}</td>"
                        f"<td>{escape(str(v.get('line')))}</td>"
                        f"<td><code>{escape(str(v.get('snippet')))}</code></td>"
                        "</tr>\n"
                    )
            html_parts.append("</table>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No security violations reported.</p>\n"
            )

        # Runtime Risk Summary (Added)
        html_parts.append("<h2>Runtime Risk Summary</h2>\n")
        html_parts.append("<table>\n<tr><th>Metric</th><th>Value</th></tr>\n")
        html_parts.append(
            f"<tr><td>Deadlocks Detected</td>"
            f"<td>{escape(str(runtime_metrics.get('deadlock_issues')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Memory Corruption Issues</td>"
            f"<td>{escape(str(runtime_metrics.get('memory_corruption_issues')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Null Pointer Dereferences</td>"
            f"<td>{escape(str(runtime_metrics.get('null_pointer_issues')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Total Runtime Risks</td>"
            f"<td>{escape(str(runtime_metrics.get('total_issues')))}</td></tr>\n"
        )
        html_parts.append("</table>\n")

        # Runtime Risk Issues (Added)
        html_parts.append("<h3>Runtime Risk Issues</h3>\n")
        if runtime_issues:
            html_parts.append("<ul>\n")
            for issue in runtime_issues[:20]:
                html_parts.append(f"<li>{escape(str(issue))}</li>\n")
            if len(runtime_issues) > 20:
                html_parts.append(
                    f"<li class='section-note'>... {len(runtime_issues) - 20} more</li>\n"
                )
            html_parts.append("</ul>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No significant runtime risks detected.</p>\n"
            )

        # Test coverage summary
        html_parts.append("<h2>Test Coverage Summary</h2>\n")
        html_parts.append("<table>\n<tr><th>Metric</th><th>Value</th></tr>\n")
        html_parts.append(
            f"<tr><td>Total source files</td>"
            f"<td>{escape(str(test_metrics.get('total_source_files')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Total test files</td>"
            f"<td>{escape(str(test_metrics.get('total_test_files')))}</td></tr>\n"
        )
        ratio = test_metrics.get("test_ratio")
        ratio_str = self._ratio_to_percent(ratio)
        html_parts.append(
            f"<tr><td>Test file ratio</td><td>{escape(ratio_str)}</td></tr>\n"
        )
        html_parts.append("</table>\n")

        # Untested sources (sample)
        untested = test_metrics.get("untested_sources") or []
        html_parts.append("<h3>Sample Untested Source Files</h3>\n")
        if untested:
            html_parts.append("<table>\n<tr><th>File</th></tr>\n")
            for src in untested[:10]:
                html_parts.append(
                    f"<tr><td><code>{escape(str(src))}</code></td></tr>\n"
                )
            if len(untested) > 10:
                html_parts.append(
                    f"<tr><td class='section-note'>... {len(untested) - 10} more</td></tr>\n"
                )
            html_parts.append("</table>\n")
        else:
            html_parts.append(
                "<p class='section-note'>No untested sources listed.</p>\n"
            )

        # Documentation summary
        html_parts.append("<h2>Documentation Summary</h2>\n")
        html_parts.append("<table>\n<tr><th>Metric</th><th>Value</th></tr>\n")
        html_parts.append(
            f"<tr><td>Overall documentation ratio</td>"
            f"<td>{escape(self._ratio_to_percent(doc_metrics.get('documentation_ratio')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Header documentation ratio</td>"
            f"<td>{escape(self._ratio_to_percent(doc_metrics.get('header_documentation_ratio')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Function implementation coverage</td>"
            f"<td>{escape(self._ratio_to_percent(doc_metrics.get('function_impl_coverage_ratio')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Type coverage</td>"
            f"<td>{escape(self._ratio_to_percent(doc_metrics.get('type_coverage_ratio')))}</td></tr>\n"
        )
        html_parts.append(
            f"<tr><td>Macro coverage</td>"
            f"<td>{escape(self._ratio_to_percent(doc_metrics.get('macro_coverage_ratio')))}</td></tr>\n"
        )
        html_parts.append("</table>\n")

        html_parts.append("</body>\n</html>\n")
        return "".join(html_parts)

    def save_html(self, output_path: str) -> str:
        """
        Generate HTML and save it to the given file path.

        Returns the absolute path of the written file.
        """
        html = self.generate_html()
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        return str(out_path.resolve())


# --------- Simple two-parameter wrapper for workflow ---------


def run_health_report(healthreport_path: str, output_dir: str) -> str:
    """
    High-level helper:
      - healthreport_path: path to healthreport.json
      - output_dir: directory where healthreport.html will be written

    Returns:
      Full path to the generated healthreport.html
    """
    gen = HealthReportHTMLGenerator.from_json_file(healthreport_path)

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    output_file = output_dir_path / "healthreport.html"
    return gen.save_html(str(output_file))


if __name__ == "__main__":
    # Simple standalone test usage:
    # Assumes healthreport.json exists at ./out/parseddata/healthreport.json
    healthreport_json = "./out/parseddata/healthreport.json"
    output_directory = "./out"  # directory, not full file path

    output_path = run_health_report(healthreport_json, output_directory)
    print(f"Health report HTML written to: {output_path}")