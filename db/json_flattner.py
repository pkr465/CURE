"""
CURE — Codebase Update & Refactor Engine
JSON Flattener: transforms healthreport.json into embedding-friendly records.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class JsonFlattener:
    """
    Flattens the healthreport.json produced by StaticAnalyzerAgent
    into a list of flat, embedding-friendly records suitable for NDJSON
    and vector DB ingestion.

    This implementation is robust to different input forms:
    - a dict already loaded from JSON,
    - a file path string to a JSON file,
    - or a raw JSON string.

    The primary entrypoint is `flatten_analysis_report`.
    """

    def __init__(self) -> None:
        # Reserved for configuration if needed later
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_health_report(
        self,
        data_or_path: Union[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Load the health report from:
        - a dict (already loaded),
        - a file path string (healthreport.json),
        - or a raw JSON string.

        Returns a Python dict representation of the report.
        """
        if isinstance(data_or_path, dict):
            # Already a parsed JSON object
            return data_or_path

        # Convert to string in case we got a Path or other object
        candidate = str(data_or_path)
        path = Path(candidate)

        # If it looks like an existing file, load it
        if path.exists() and path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)

        # Otherwise, assume it's a raw JSON string
        try:
            return json.loads(candidate)
        except Exception as e:
            raise ValueError(
                f"JsonFlattener: unable to interpret input as dict, path, or JSON string: {e}"
            ) from e

    @staticmethod
    def _enrich_file_fields(record: Dict[str, Any]) -> None:
        """Populate ``file_relative_path`` and ``file_name`` from ``file_path``.

        The NDJSONProcessor UUID keys reference ``file_relative_path`` and
        ``file_name``, but flattened records only carry ``file_path``.  This
        helper bridges the gap so file identity is included in dedup UUIDs.
        """
        fp = record.get("file_path")
        if fp and isinstance(fp, str):
            record.setdefault("file_relative_path", fp)
            record.setdefault("file_name", Path(fp).name)

    def _add_record_if_valid(
        self,
        records: List[Dict[str, Any]],
        record: Dict[str, Any],
    ) -> None:
        """
        Utility to append a record only if it has at least one non-None value.

        You can customize the filtering logic here if needed.
        """
        if not record:
            return
        if not isinstance(record, dict):
            # We only expect dicts as records
            return
        # If every value is None/empty, skip
        if all(v is None or v == "" for v in record.values()):
            return
        # Ensure file_relative_path / file_name are present for UUID dedup
        self._enrich_file_fields(record)
        records.append(record)

    # ------------------------------------------------------------------
    # Flattening logic
    # ------------------------------------------------------------------

    def flatten_analysis_report(
        self,
        data_or_path: Union[str, Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Flatten the health report into a list of records.

        Parameters:
        - data_or_path:
            * a dict (already loaded health report),
            * a file path string to a healthreport.json file, or
            * raw JSON string.
        - output_path:
            If provided, write the flattened records as NDJSON
            (one JSON object per line) to this file.

        Returns:
        - List of flattened record dicts.
        """

        data: Dict[str, Any] = self._load_health_report(data_or_path)

        # Top-level sections from the healthreport.json
        summary: Dict[str, Any] = data.get("summary", {}) or {}
        dependency_graph: Dict[str, Any] = data.get("dependency_graph", {}) or {}
        documentation: Dict[str, Any] = data.get("documentation", {}) or {}
        modularization_plan: Dict[str, Any] = data.get("modularization_plan", {}) or {}
        validation_report: Dict[str, Any] = data.get("validation_report", {}) or {}
        health_metrics: Dict[str, Any] = data.get("health_metrics", {}) or {}

        records: List[Dict[str, Any]] = []

        # --------------------------------------------------------------
        # 1) High-level codebase summary record
        # --------------------------------------------------------------
        file_stats: Dict[str, Any] = summary.get("file_stats", {}) or {}
        overall_health: Dict[str, Any] = health_metrics.get("overall_health", {}) or {}

        # Per-metric scores (if present)
        dependency_score = (health_metrics.get("dependency_score") or {}).get("score")
        quality_score = (health_metrics.get("quality_score") or {}).get("score")
        complexity_score = (health_metrics.get("complexity_score") or {}).get("score")
        maintainability_score = (
            (health_metrics.get("maintainability_score") or {}).get("score")
        )
        documentation_score = (
            (health_metrics.get("documentation_score") or {}).get("score")
        )
        test_coverage_score = (
            (health_metrics.get("test_coverage_score") or {}).get("score")
        )
        security_score = (health_metrics.get("security_score") or {}).get("score")

        summary_record: Dict[str, Any] = {
            "record_type": "codebase_summary",
            "id": "codebase_summary",
            "source": "healthreport.json",
            # Basic stats
            "total_files": file_stats.get("total_files"),
            "total_lines": file_stats.get("total_lines"),
            "languages": file_stats.get("languages"),
            # Overall health
            "overall_score": overall_health.get("score"),
            "overall_grade": overall_health.get("grade"),
            "overall_status": overall_health.get("status"),
            "overall_recommendation": overall_health.get("recommendation"),
            # Per-metric scores
            "dependency_score": dependency_score,
            "quality_score": quality_score,
            "complexity_score": complexity_score,
            "maintainability_score": maintainability_score,
            "documentation_score": documentation_score,
            "test_coverage_score": test_coverage_score,
            "security_score": security_score,
        }
        self._add_record_if_valid(records, summary_record)

        # --------------------------------------------------------------
        # 2) Dependency graph summary record (from validation + metrics)
        # --------------------------------------------------------------
        base_validation: Dict[str, Any] = (
            validation_report.get("base_validation", {}) or {}
        )
        circular_deps = base_validation.get("circular_dependencies") or []
        dep_score_data: Dict[str, Any] = (
            health_metrics.get("dependency_score", {}) or {}
        )

        dep_record: Dict[str, Any] = {
            "record_type": "dependency_summary",
            "id": "dependency_summary",
            "source": "healthreport.json",
            "has_cycles": bool(circular_deps),
            "cycle_count": len(circular_deps),
            "max_fan_in": None,   # Not computed in this report
            "max_fan_out": None,  # Not computed in this report
            "notes": "; ".join(dep_score_data.get("issues") or []),
        }
        self._add_record_if_valid(records, dep_record)

        # --------------------------------------------------------------
        # 3) Metric-level summary records (one per top-level metric)
        # --------------------------------------------------------------
        def add_metric_summary(metric_key: str, pretty_name: str) -> None:
            metric_data: Dict[str, Any] = health_metrics.get(metric_key, {}) or {}
            if not metric_data:
                return
            metrics = metric_data.get("metrics") or {}
            issues = metric_data.get("issues") or []
            rec = {
                "record_type": "metric_summary",
                "id": f"metric::{metric_key}",
                "source": "healthreport.json",
                "metric_key": metric_key,
                "metric_name": pretty_name,
                "score": metric_data.get("score"),
                "grade": metric_data.get("grade"),
                "status": metric_data.get("status"),
                "metrics": metrics,
                "issues": issues,
                "recommendations": metric_data.get("recommendations"),
            }
            self._add_record_if_valid(records, rec)

        add_metric_summary("quality_score", "Code quality")
        add_metric_summary("complexity_score", "Complexity")
        add_metric_summary("maintainability_score", "Maintainability")
        add_metric_summary("documentation_score", "Documentation")
        add_metric_summary("test_coverage_score", "Test coverage")
        add_metric_summary("security_score", "Security")
        add_metric_summary("dependency_score", "Dependency health")

        # --------------------------------------------------------------
        # 4) Documentation-related summary record (from doc metrics)
        # --------------------------------------------------------------
        doc_score: Dict[str, Any] = (
            health_metrics.get("documentation_score", {}) or {}
        )
        doc_metrics: Dict[str, Any] = doc_score.get("metrics", {}) or {}

        documentation_ratio = doc_metrics.get("documentation_ratio")
        missing_items = doc_metrics.get("missing_items") or []
        average_doc_density = doc_metrics.get("average_doc_density")
        quality_doc_blocks_ratio = doc_metrics.get("quality_doc_blocks_ratio")

        doc_record: Dict[str, Any] = {
            "record_type": "documentation_summary",
            "id": "documentation_summary",
            "source": "healthreport.json",
            "coverage": documentation_ratio,
            "quality": quality_doc_blocks_ratio or average_doc_density,
            "gaps": len(missing_items),
            "recommendations": doc_score.get("issues"),
        }
        self._add_record_if_valid(records, doc_record)

        # Optional documentation items (forward compatibility)
        doc_items = documentation.get("items") or documentation.get("files") or []
        if isinstance(doc_items, list):
            for item in doc_items:
                if not isinstance(item, dict):
                    continue
                file_path = item.get("file") or item.get("file_path")
                rec = {
                    "record_type": "documentation_item",
                    "id": f"doc_item::{file_path}" if file_path else "doc_item",
                    "source": "healthreport.json",
                    "file_path": file_path,
                    "module": item.get("module"),
                    "summary": item.get("summary"),
                    "comments": item.get("comments"),
                    "issues": item.get("issues"),
                    "recommendations": item.get("recommendations"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 5) Modularization plan summary
        # --------------------------------------------------------------
        base_plan = modularization_plan.get("base_plan") or {}
        llm_enhanced = modularization_plan.get("llm_enhanced_plan") or {}
        llm_error = llm_enhanced.get("error")

        mod_goals = None
        if base_plan:
            mod_goals = (
                "Baseline modularization plan generated "
                "(all modules currently marked 'keep')."
            )

        mod_recommendations: Optional[str] = None
        if llm_error:
            mod_recommendations = (
                f"LLM-enhanced modularization plan unavailable: {llm_error}"
            )

        mod_plan_record: Dict[str, Any] = {
            "record_type": "modularization_summary",
            "id": "modularization_summary",
            "source": "healthreport.json",
            "goals": mod_goals,
            "benefits": None,
            "risks": None,
            "recommendations": mod_recommendations,
        }
        self._add_record_if_valid(records, mod_plan_record)

        # Optional modularization steps (forward compatibility)
        mod_steps = modularization_plan.get("steps", [])
        if isinstance(mod_steps, list):
            for idx, step in enumerate(mod_steps):
                if not isinstance(step, dict):
                    continue
                step_title = step.get("title", idx)
                rec = {
                    "record_type": "modularization_step",
                    "id": f"mod_step::{step_title}",
                    "source": "healthreport.json",
                    "step_index": idx,
                    "title": step.get("title"),
                    "description": step.get("description"),
                    "impact": step.get("impact"),
                    "priority": step.get("priority"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 6) Validation report summary
        # --------------------------------------------------------------
        base_validation = validation_report.get("base_validation") or {}
        llm_validation = validation_report.get("llm_validation") or {}

        val_passed = base_validation.get("validation_passed")
        circular_deps = base_validation.get("circular_dependencies") or []
        val_error = llm_validation.get("error")

        val_record: Dict[str, Any] = {
            "record_type": "validation_summary",
            "id": "validation_summary",
            "source": "healthreport.json",
            "status": "passed" if val_passed else "failed",
            "issues": len(circular_deps),
            "warnings": val_error,
            "notes": (
                "No circular dependencies detected."
                if val_passed and not circular_deps
                else None
            ),
        }
        self._add_record_if_valid(records, val_record)

        # Optional per-check validation entries
        checks = validation_report.get("checks", [])
        if isinstance(checks, list):
            for idx, check in enumerate(checks):
                if not isinstance(check, dict):
                    continue
                check_name = check.get("name", idx)
                rec = {
                    "record_type": "validation_check",
                    "id": f"validation_check::{check_name}",
                    "source": "healthreport.json",
                    "check_name": check_name,
                    "status": check.get("status"),
                    "details": check.get("details"),
                    "severity": check.get("severity"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 7) Quality violations (from quality_score.metrics.violations)
        # --------------------------------------------------------------
        quality_data: Dict[str, Any] = (
            health_metrics.get("quality_score", {}) or {}
        )
        quality_metrics: Dict[str, Any] = quality_data.get("metrics", {}) or {}
        quality_violations = quality_metrics.get("violations") or []
        if isinstance(quality_violations, list):
            for idx, v in enumerate(quality_violations):
                if not isinstance(v, dict):
                    continue
                q_file = v.get("file", "")
                q_line = v.get("line", "")
                q_rule = v.get("rule", "")
                rec = {
                    "record_type": "quality_violation",
                    "id": f"quality_violation::{q_file}::{q_line}::{q_rule}",
                    "source": "healthreport.json",
                    "rule": q_rule,
                    "severity": v.get("severity"),
                    "file_path": q_file,
                    "line": q_line,
                    "message": v.get("message"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 8) Security violations (from security_score.metrics.violations)
        # --------------------------------------------------------------
        security_data: Dict[str, Any] = (
            health_metrics.get("security_score", {}) or {}
        )
        security_metrics: Dict[str, Any] = security_data.get("metrics", {}) or {}
        security_violations = security_metrics.get("violations") or []
        if isinstance(security_violations, list):
            for idx, v in enumerate(security_violations):
                if not isinstance(v, dict):
                    continue
                s_file = v.get("file", "")
                s_line = v.get("line", "")
                s_rule = v.get("rule", "")
                rec = {
                    "record_type": "security_violation",
                    "id": f"security_violation::{s_file}::{s_line}::{s_rule}",
                    "source": "healthreport.json",
                    "rule": s_rule,
                    "severity": v.get("severity"),
                    "file_path": s_file,
                    "line": s_line,
                    "message": v.get("message"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 9) Complexity hotspots (from complexity_score.metrics.top_complex_functions)
        # --------------------------------------------------------------
        complexity_data: Dict[str, Any] = (
            health_metrics.get("complexity_score", {}) or {}
        )
        complexity_metrics: Dict[str, Any] = complexity_data.get("metrics", {}) or {}
        top_functions = complexity_metrics.get("top_complex_functions") or []
        if isinstance(top_functions, list):
            for idx, fn in enumerate(top_functions):
                if not isinstance(fn, dict):
                    continue
                c_file = fn.get("file", "")
                c_func = fn.get("name", "")
                rec = {
                    "record_type": "complexity_hotspot",
                    "id": f"complexity_hotspot::{c_file}::{c_func}",
                    "source": "healthreport.json",
                    "function": c_func,
                    "file_path": c_file,
                    "cyclomatic_complexity": fn.get("cyclomatic_complexity"),
                    "cognitive_complexity": fn.get("cognitive_complexity"),
                    "lines_of_code": fn.get("lines_of_code"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 10) File-level health records (if present under various metrics)
        # --------------------------------------------------------------
        def add_file_health_from_metric(
            metric_key: str, metric_label: str, file_key: str
        ) -> None:
            metric_data: Dict[str, Any] = health_metrics.get(metric_key, {}) or {}
            metrics = metric_data.get("metrics", {}) or {}
            files = metrics.get(file_key) or []
            if not isinstance(files, list):
                return
            for item in files:
                if not isinstance(item, dict):
                    continue
                file_path = item.get("file") or item.get("file_path")
                rec = {
                    "record_type": "file_health",
                    "id": f"file_health::{metric_key}::{file_path}",
                    "source": "healthreport.json",
                    "metric_key": metric_key,
                    "metric_name": metric_label,
                    "file_path": file_path,
                    "details": item,
                }
                self._add_record_if_valid(records, rec)

        add_file_health_from_metric("maintainability_score", "Maintainability", "files")
        add_file_health_from_metric("documentation_score", "Documentation", "files")
        add_file_health_from_metric("test_coverage_score", "Test coverage", "files")

        # --------------------------------------------------------------
        # 11) Untested sources (from test_coverage_score.metrics.untested_sources)
        # --------------------------------------------------------------
        test_data: Dict[str, Any] = (
            health_metrics.get("test_coverage_score", {}) or {}
        )
        test_metrics: Dict[str, Any] = test_data.get("metrics", {}) or {}
        untested_sources = test_metrics.get("untested_sources") or []
        if isinstance(untested_sources, list):
            for file_path in untested_sources:
                if not isinstance(file_path, str):
                    continue
                rec = {
                    "record_type": "untested_source",
                    "id": f"untested_source::{file_path}",
                    "source": "healthreport.json",
                    "file_path": file_path,
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 12) Critical issues (from overall_health.critical_issues)
        # --------------------------------------------------------------
        overall_health = health_metrics.get("overall_health", {}) or {}
        critical_issues = overall_health.get("critical_issues") or []
        if isinstance(critical_issues, list):
            for idx, issue in enumerate(critical_issues):
                if not isinstance(issue, dict):
                    continue
                ci_title = issue.get("title", idx)
                ci_category = issue.get("category", "")
                rec = {
                    "record_type": "critical_issue",
                    "id": f"critical_issue::{ci_title}::{ci_category}",
                    "source": "healthreport.json",
                    "title": ci_title,
                    "severity": issue.get("severity"),
                    "category": ci_category,
                    "details": issue.get("details"),
                    "recommendations": issue.get("recommendations"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 13) Optional dependency nodes/edges (forward-compatible schema)
        # --------------------------------------------------------------
        nodes = dependency_graph.get("nodes", [])
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id") or node.get("name")
                rec = {
                    "record_type": "dependency_node",
                    "id": f"dep_node::{node_id}" if node_id else "dep_node",
                    "source": "healthreport.json",
                    "node_id": node_id,
                    "file_path": node.get("file_path"),
                    "module": node.get("module"),
                    "fan_in": node.get("fan_in"),
                    "fan_out": node.get("fan_out"),
                    "tags": node.get("tags"),
                }
                self._add_record_if_valid(records, rec)

        edges = dependency_graph.get("edges", [])
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                src = edge.get("source") or edge.get("from")
                tgt = edge.get("target") or edge.get("to")
                rec = {
                    "record_type": "dependency_edge",
                    "id": f"dep_edge::{src}->{tgt}" if src and tgt else "dep_edge",
                    "source": "healthreport.json",
                    "from": src,
                    "to": tgt,
                    "weight": edge.get("weight"),
                    "type": edge.get("type"),
                }
                self._add_record_if_valid(records, rec)

        # --------------------------------------------------------------
        # 14) Deep static adapter results (from adapters section)
        # --------------------------------------------------------------
        adapters: Dict[str, Any] = data.get("adapters", {}) or {}
        if not adapters:
            # Also check inside health_metrics for adapter results
            adapters = health_metrics.get("adapters", {}) or {}

        for adapter_name, adapter_data in adapters.items():
            if not isinstance(adapter_data, dict):
                continue

            # Adapter-level summary record
            adapter_summary: Dict[str, Any] = {
                "record_type": "adapter_summary",
                "id": f"adapter::{adapter_name}",
                "source": "healthreport.json",
                "adapter_name": adapter_name,
                "score": adapter_data.get("score"),
                "grade": adapter_data.get("grade"),
                "tool_available": adapter_data.get("tool_available"),
                "metrics": adapter_data.get("metrics"),
                "issues": adapter_data.get("issues"),
            }
            self._add_record_if_valid(records, adapter_summary)

            # Per-finding detail records
            details = adapter_data.get("details") or []
            if isinstance(details, list):
                for idx, detail in enumerate(details):
                    if not isinstance(detail, dict):
                        continue
                    af_file = detail.get("file", "")
                    af_line = detail.get("line", "")
                    af_func = detail.get("function", "")
                    rec = {
                        "record_type": "adapter_finding",
                        "id": f"adapter_finding::{adapter_name}::{af_file}::{af_line}::{af_func}",
                        "source": "healthreport.json",
                        "adapter_name": adapter_name,
                        "file_path": af_file,
                        "function": af_func,
                        "line": af_line,
                        "description": detail.get("description"),
                        "severity": detail.get("severity"),
                        "category": detail.get("category"),
                        "cwe": detail.get("cwe"),
                    }
                    self._add_record_if_valid(records, rec)

        logger.info(
            "Flattened %d records from healthreport (incl. %d adapter entries)",
            len(records),
            sum(1 for r in records if r.get("record_type", "").startswith("adapter_")),
        )

        # --------------------------------------------------------------
        # Write output as NDJSON if requested
        # --------------------------------------------------------------
        if output_path is not None:
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8") as f:
                for rec in records:
                    # Each line is a single JSON object (dict)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        return records


def main() -> None:
    """
    Simple CLI entrypoint (optional). You can run:
      python db/json_flattner.py path/to/healthreport.json path/to/healthreport_flat.json
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Flatten healthreport.json into NDJSON records."
    )
    parser.add_argument(
        "input",
        help="Path to healthreport.json (or compatible report).",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Optional path to write NDJSON. If omitted, prints records as JSON array to stdout.",
    )
    args = parser.parse_args()

    flattener = JsonFlattener()
    records = flattener.flatten_analysis_report(args.input, args.output)

    if args.output is None:
        # If no output file provided, print a JSON array to stdout
        print(json.dumps(records, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()