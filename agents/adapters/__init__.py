"""
Adapter layer for dependency_builder-powered static analysis.

Adapters bridge CCLSCodeNavigator (libclang/ccls), Lizard, and Flawfinder
into the health report pipeline. MetricsCalculator orchestrates them.
"""

from agents.adapters.base_adapter import BaseStaticAdapter

try:
    from agents.adapters.dead_code_adapter import DeadCodeAdapter
except ImportError:
    DeadCodeAdapter = None

try:
    from agents.adapters.ast_complexity_adapter import ASTComplexityAdapter
except ImportError:
    ASTComplexityAdapter = None

try:
    from agents.adapters.security_adapter import SecurityAdapter
except ImportError:
    SecurityAdapter = None

try:
    from agents.adapters.call_graph_adapter import CallGraphAdapter
except ImportError:
    CallGraphAdapter = None

try:
    from agents.adapters.function_metrics_adapter import FunctionMetricsAdapter
except ImportError:
    FunctionMetricsAdapter = None

try:
    from agents.adapters.excel_report_adapter import ExcelReportAdapter
except ImportError:
    ExcelReportAdapter = None

__all__ = [
    "BaseStaticAdapter",
    "DeadCodeAdapter",
    "ASTComplexityAdapter",
    "SecurityAdapter",
    "CallGraphAdapter",
    "FunctionMetricsAdapter",
    "ExcelReportAdapter",
]
