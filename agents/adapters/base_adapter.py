"""
Base adapter interface for dependency_builder-powered static analysis.

All adapters inherit from BaseStaticAdapter and implement analyze().
Results follow a standard schema so MetricsCalculator can orchestrate
them uniformly and ExcelReportAdapter can generate consistent tabs.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseStaticAdapter(ABC):
    """
    Abstract base class for static analysis adapters.

    Subclasses must implement analyze() and return a standardised dict:
        {
            "score":          float   0-100,
            "grade":          str     A/B/C/D/F,
            "metrics":        dict    adapter-specific aggregate numbers,
            "issues":         list    human-readable summary strings,
            "details":        list    per-finding dicts (for Excel export),
            "tool_available": bool    False if underlying tool was missing,
        }
    """

    def __init__(self, adapter_name: str, debug: bool = False):
        self.adapter_name = adapter_name
        self.debug = debug
        self.logger = logging.getLogger(f"adapters.{adapter_name}")

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------
    @abstractmethod
    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run analysis and return standardised result dict."""
        ...

    # ------------------------------------------------------------------
    # Helpers available to all subclasses
    # ------------------------------------------------------------------
    @staticmethod
    def _score_to_grade(score: float) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    def _handle_tool_unavailable(
        self, tool_name: str, fallback_message: str = ""
    ) -> Dict[str, Any]:
        """Standard degradation response when a required tool is missing."""
        msg = fallback_message or f"{tool_name} not available; skipping {self.adapter_name}"
        self.logger.warning(msg)
        return {
            "score": 0.0,
            "grade": "F",
            "metrics": {"tool_available": False, "fallback_reason": msg},
            "issues": [msg],
            "details": [],
            "tool_available": False,
        }

    @staticmethod
    def _make_detail(
        file: str,
        function: str,
        line: int,
        description: str,
        severity: str = "medium",
        category: str = "general",
        cwe: str = "",
    ) -> Dict[str, Any]:
        """Construct a single finding row for the Excel export."""
        return {
            "file": file,
            "function": function or "",
            "line": line,
            "description": description,
            "severity": severity,
            "category": category,
            "cwe": cwe,
        }

    def _empty_result(self, reason: str = "No files to analyze") -> Dict[str, Any]:
        return {
            "score": 0.0,
            "grade": "F",
            "metrics": {"tool_available": True},
            "issues": [reason] if reason else [],
            "details": [],
            "tool_available": True,
        }
