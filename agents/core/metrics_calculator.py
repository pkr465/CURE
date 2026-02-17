"""
Health metrics calculation coordinator for C/C++ codebases (enhanced).

Orchestrates:
- 9 existing regex/heuristic analyzers (quality, complexity, security, etc.)
- Deep static analysis adapters (CCLS/libclang, Lizard, Flawfinder)
"""

from typing import Dict, List, Any, Optional
import json
import logging
import os

logger = logging.getLogger(__name__)

# Existing Analyzers
from agents.analyzers.quality_analyzer import QualityAnalyzer
from agents.analyzers.complexity_analyzer import ComplexityAnalyzer
from agents.analyzers.security_analyzer import SecurityAnalyzer
from agents.analyzers.documentation_analyzer import DocumentationAnalyzer
from agents.analyzers.maintainability_analyzer import MaintainabilityAnalyzer
from agents.analyzers.test_coverage_analyzer import TestCoverageAnalyzer
from agents.analyzers.potential_deadlock_analyzer import PotentialDeadlockAnalyzer
from agents.analyzers.null_pointer_analyzer import NullPointerAnalyzer
from agents.analyzers.memory_corruption_analyzer import MemoryCorruptionAnalyzer

# Deep static analysis adapters (optional — graceful degradation)
try:
    from agents.adapters import (
        DeadCodeAdapter,
        ASTComplexityAdapter,
        SecurityAdapter,
        CallGraphAdapter,
        FunctionMetricsAdapter,
        ExcelReportAdapter,
    )
    ADAPTERS_AVAILABLE = True
except ImportError as _e:
    ADAPTERS_AVAILABLE = False
    logger.info(f"Static analysis adapters not available: {_e}")


class MetricsCalculator:
    """
    Coordinates calculation of all health metrics for C/C++ codebases.
    """

    def __init__(
        self,
        codebase_path: str,
        output_dir: str,
        project_root: Optional[str] = None,
        debug: bool = False,
        enable_adapters: bool = False,
    ):
        """Initialize metrics calculator with all analyzers."""
        self.codebase_path = codebase_path
        self.project_root = project_root
        self.debug = debug
        self.output_dir = output_dir

        # Standard Analyzers
        self.quality_analyzer = QualityAnalyzer()
        self.complexity_analyzer = ComplexityAnalyzer()
        self.security_analyzer = SecurityAnalyzer()
        self.documentation_analyzer = DocumentationAnalyzer()
        self.maintainability_analyzer = MaintainabilityAnalyzer(
            codebase_path=codebase_path,
            project_root=project_root,
            debug=debug,
        )
        self.test_coverage_analyzer = TestCoverageAnalyzer()
        
        # New Runtime Analyzers (Initialized with debug flag)
        self.deadlock_analyzer = PotentialDeadlockAnalyzer(debug=debug)
        self.null_pointer_analyzer = NullPointerAnalyzer(debug=debug)
        self.memory_corruption_analyzer = MemoryCorruptionAnalyzer(debug=debug)

        # Deep static analysis adapters (optional — require both flag and availability)
        self.adapters_enabled = enable_adapters and ADAPTERS_AVAILABLE
        if self.adapters_enabled:
            self.dead_code_adapter = DeadCodeAdapter(debug=debug)
            self.ast_complexity_adapter = ASTComplexityAdapter(debug=debug)
            self.security_adapter = SecurityAdapter(debug=debug)
            self.call_graph_adapter = CallGraphAdapter(debug=debug)
            self.function_metrics_adapter = FunctionMetricsAdapter(debug=debug)
            self.excel_report_adapter = ExcelReportAdapter(
                output_dir=output_dir, debug=debug
            )
    
    def _write_metric_report(self, metric_name: str, data: Any) -> None:
        """Write an individual metric report file into the output_dir."""
        filename = f"{metric_name}.json"
        report_path = os.path.join(self.output_dir, filename)

        payload = {
            "metric_name": metric_name,
            "data": data,
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    
    def calculate_all_metrics(
        self,
        file_cache: List[Dict[str, Any]],
        dependency_graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive health metrics for a C/C++ codebase.
        """
        metrics: Dict[str, Any] = {}

        # 1. Dependency
        metrics["dependency_score"] = self._calculate_dependency_score(dependency_graph)
        self._write_metric_report("dependency_score", metrics["dependency_score"])

        # 2. Quality
        metrics["quality_score"] = self.quality_analyzer.analyze(file_cache)
        self._write_metric_report("quality_score", metrics["quality_score"])

        # 3. Complexity
        metrics["complexity_score"] = self.complexity_analyzer.analyze(file_cache)
        self._write_metric_report("complexity_score", metrics["complexity_score"])

        # 4. Security
        metrics["security_score"] = self.security_analyzer.analyze(file_cache)
        self._write_metric_report("security_score", metrics["security_score"])
        
        # 5. Documentation
        metrics["documentation_score"] = self.documentation_analyzer.analyze(file_cache)
        self._write_metric_report("documentation_score", metrics["documentation_score"])
        
        # 6. Maintainability
        metrics["maintainability_score"] = self.maintainability_analyzer.analyze(
            file_cache, dependency_graph
        )
        self._write_metric_report("maintainability_score", metrics["maintainability_score"])
        
        # 7. Test Coverage
        metrics["test_coverage_score"] = self.test_coverage_analyzer.analyze(file_cache)
        self._write_metric_report("test_coverage_score", metrics["test_coverage_score"])
        
        # 8. Runtime Risk (New)
        metrics["runtime_risk_score"] = self._calculate_runtime_risk_score(file_cache)
        self._write_metric_report("runtime_risk_score", metrics["runtime_risk_score"])
        
        # 9. Deep static analysis adapters
        if self.adapters_enabled:
            metrics["adapters"] = self._run_adapters(file_cache, dependency_graph)
            self._write_metric_report("adapter_results", metrics["adapters"])

        # 10. Overall
        metrics["overall_health"] = self._calculate_overall_health_score(metrics)

        return metrics

    # ------------------------------------------------------------------ #
    # Deep Static Analysis Adapters
    # ------------------------------------------------------------------ #

    def _init_ccls_navigator(self) -> Optional[Any]:
        """
        Create a shared CCLSCodeNavigator for adapter use.

        Returns None if ccls or libclang are not available — adapters
        that require CCLS will degrade gracefully.
        """
        try:
            from dependency_builder.ccls_code_navigator import CCLSCodeNavigator
            from dependency_builder.config import DependencyBuilderConfig

            config = DependencyBuilderConfig.from_env()
            cache_path = os.path.join(self.output_dir, ".ccls_cache")
            os.makedirs(cache_path, exist_ok=True)

            navigator = CCLSCodeNavigator(
                project_root=self.codebase_path,
                cache_path=cache_path,
                logger=logger,
                config=config,
            )
            return navigator
        except Exception as exc:
            logger.warning(f"CCLSCodeNavigator init failed (adapters will degrade): {exc}")
            return None

    def _run_adapters(
        self,
        file_cache: List[Dict[str, Any]],
        dependency_graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute all deep static analysis adapters and generate the Excel report.

        Returns a dict mapping adapter names to their result dicts.
        """
        ccls_navigator = None
        adapter_results: Dict[str, Any] = {}

        try:
            # Initialize CCLS once for all adapters that need it
            ccls_navigator = self._init_ccls_navigator()

            # Run each adapter (order: standalone tools first, CCLS-dependent second)
            adapters = [
                ("ast_complexity", self.ast_complexity_adapter),
                ("security", self.security_adapter),
                ("dead_code", self.dead_code_adapter),
                ("call_graph", self.call_graph_adapter),
                ("function_metrics", self.function_metrics_adapter),
            ]

            for name, adapter in adapters:
                try:
                    result = adapter.analyze(
                        file_cache,
                        ccls_navigator=ccls_navigator,
                        dependency_graph=dependency_graph,
                    )
                    adapter_results[name] = result
                    score = result.get("score", 0)
                    grade = result.get("grade", "F")
                    avail = result.get("tool_available", False)
                    logger.info(
                        f"Adapter {name}: score={score} grade={grade} tool_available={avail}"
                    )
                except Exception as exc:
                    logger.warning(f"Adapter {name} failed: {exc}")
                    adapter_results[name] = {
                        "score": 0.0,
                        "grade": "F",
                        "metrics": {"error": str(exc)},
                        "issues": [f"Adapter failed: {exc}"],
                        "details": [],
                        "tool_available": False,
                    }

            # Generate detailed_code_review.xlsx from adapter results
            try:
                self.excel_report_adapter.analyze(
                    file_cache=[],
                    adapter_results=adapter_results,
                )
                logger.info("detailed_code_review.xlsx generated successfully")
            except Exception as exc:
                logger.warning(f"Excel report generation failed: {exc}")

        finally:
            # Clean up CCLS process
            if ccls_navigator is not None:
                try:
                    ccls_navigator.killCCLSProcess()
                except Exception:
                    pass

        return adapter_results

    # ------------------------------------------------------------------ #
    # New: Runtime Risk Score Aggregation
    # ------------------------------------------------------------------ #

    def _calculate_runtime_risk_score(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs comprehensive runtime analyzers and computes a risk score.
        """
        # Execute Analyzers
        deadlock_res = self.deadlock_analyzer.analyze(file_cache)
        null_res = self.null_pointer_analyzer.analyze(file_cache)
        mem_res = self.memory_corruption_analyzer.analyze(file_cache)
        
        # Aggregate Issues
        all_issues = []
        all_issues.extend(deadlock_res.get("issues", []))
        all_issues.extend(null_res.get("issues", []))
        all_issues.extend(mem_res.get("issues", []))

        # Scoring Logic (Start at 100)
        score = 100.0
        
        deadlock_count = len(deadlock_res.get("issues", []))
        mem_count = len(mem_res.get("issues", []))
        null_count = len(null_res.get("issues", []))
        
        score -= (deadlock_count * 15.0)
        score -= (mem_count * 12.0)
        score -= (null_count * 5.0)
        
        score = max(0.0, score)
        grade = self._score_to_grade(score)
        
        runtime_metrics = {
            "deadlock_issues": deadlock_count,
            "memory_corruption_issues": mem_count,
            "null_pointer_issues": null_count,
            "details": {
                "deadlock": deadlock_res.get("metrics", []),
                "memory": mem_res.get("metrics", []),
                "null_pointer": null_res.get("metrics", [])
            }
        }

        return {
            "score": round(score, 1),
            "grade": grade,
            "issues": all_issues,
            "metrics": runtime_metrics
        }

    # ------------------------------------------------------------------ #
    # Dependency score
    # ------------------------------------------------------------------ #

    def _calculate_dependency_score(
        self, dependency_graph: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not dependency_graph or "analysis" not in dependency_graph:
            return {
                "score": 0.0, "grade": "F", "issues": ["No dependency graph available"], "metrics": {}
            }

        analysis = dependency_graph.get("analysis", {}) or {}
        score = 100.0
        issues: List[str] = []

        cycle_count = analysis.get("cycle_count", 0)
        if cycle_count > 0:
            penalty = min(40.0, float(cycle_count) * 10.0)
            score -= penalty
            issues.append(f"{cycle_count} circular dependencies detected")

        max_fan_out = analysis.get("max_fan_out", 0)
        if max_fan_out > 15:
            penalty = min(20.0, float(max_fan_out - 15) * 2.0)
            score -= penalty
            issues.append(f"High coupling detected (max fan-out: {max_fan_out})")

        missing_count = analysis.get("external_categories", {}).get("missing", 0)
        if missing_count > 0:
            penalty = min(15.0, float(missing_count) * 3.0)
            score -= penalty
            issues.append(f"{missing_count} missing include file(s)")

        header_to_source_ratio = analysis.get("header_to_source_ratio", 1.0)
        if header_to_source_ratio > 3.0:
            score -= 10.0
            issues.append(f"High header-to-source ratio ({header_to_source_ratio:.1f}:1)")

        is_internal_connected = analysis.get("is_internal_connected")
        if is_internal_connected is True:
            score += 5.0
        elif is_internal_connected is False:
            score -= 10.0
            issues.append("Internal dependency graph is not fully connected")

        score = max(0.0, min(100.0, score))
        grade = self._score_to_grade(score)

        return {
            "score": round(score, 1),
            "grade": grade,
            "issues": issues,
            "metrics": {
                "circular_dependencies": cycle_count,
                "max_fan_out": max_fan_out,
                "missing_includes": missing_count,
            },
        }

    # ------------------------------------------------------------------ #
    # Overall health score
    # ------------------------------------------------------------------ #

    def _calculate_overall_health_score(
        self, metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not metrics:
            return {"score": 0.0, "grade": "F", "recommendation": "No data"}

        base_weights = {
            "runtime_risk_score": 0.25,
            "security_score": 0.20,
            "quality_score": 0.15,
            "maintainability_score": 0.15,
            "complexity_score": 0.10,
            "dependency_score": 0.05,
            "documentation_score": 0.05,
            "test_coverage_score": 0.05,
        }

        active_weights = {k: w for k, w in base_weights.items() if k in metrics}
        total_w = sum(active_weights.values())

        if total_w <= 0:
            return {"score": 0.0, "grade": "F", "recommendation": "No valid metrics found"}

        weights_used = {k: v / total_w for k, v in active_weights.items()}

        weighted_score = 0.0
        contributions = []

        for name, weight in weights_used.items():
            metric_data = metrics.get(name, {}) or {}
            score = float(metric_data.get("score", 0.0))
            weighted_score += score * weight
            contributions.append({
                "metric": name, "score": score, "weight": weight, "grade": metric_data.get("grade", "F")
            })

        overall_score = weighted_score
        gates_applied = []

        rr_score = float(metrics.get("runtime_risk_score", {}).get("score", 100.0))
        if rr_score < 50.0:
            overall_score = min(overall_score, 45.0)
            gates_applied.append("Runtime Risk Gate: Critical runtime issues detected.")

        sec_score = float(metrics.get("security_score", {}).get("score", 100.0))
        if sec_score < 40.0:
            overall_score = min(overall_score, 40.0)
            gates_applied.append("Security Gate: High vulnerability count.")

        qual_score = float(metrics.get("quality_score", {}).get("score", 100.0))
        if qual_score < 40.0:
            overall_score = min(overall_score, 50.0)
            gates_applied.append("Quality Gate: Code quality too low.")

        overall_score = round(max(0.0, min(100.0, overall_score)), 1)
        grade = self._score_to_grade(overall_score)

        critical_issues = []
        for name, data in metrics.items():
            if isinstance(data, dict):
                if data.get("score", 100) < 60:
                    critical_issues.extend(data.get("issues", [])[:3])
        
        deduped_issues = sorted(list(set(critical_issues)))[:10]

        return {
            "score": overall_score,
            "grade": grade,
            "weights_used": weights_used,
            "gates_applied": gates_applied,
            "contributions": contributions,
            "critical_issues": deduped_issues,
            "recommendation": self._get_health_recommendation(overall_score),
            "pre_gate_score": round(weighted_score, 1),
        }

    def _get_health_recommendation(self, score: float) -> str:
        if score >= 90: return "Excellent stability."
        if score >= 75: return "Good health."
        if score >= 60: return "Average. Check runtime risks."
        if score >= 40: return "Poor. Critical issues detected."
        return "Critical instability."

    @staticmethod
    def _score_to_grade(score: float) -> str:
        if score >= 90: return "A"
        if score >= 80: return "B"
        if score >= 70: return "C"
        if score >= 60: return "D"
        return "F"