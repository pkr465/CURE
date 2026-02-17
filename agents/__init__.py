"""
CURE — Codebase Update & Refactor Engine
Agents Package

Primary agents:
    StaticAnalyzerAgent  — Unified 7-phase pipeline for static analysis and health reporting.
    CodebasePatchAgent   — Patch analysis agent (diff original vs patched, report new issues).
"""

from .codebase_static_agent import StaticAnalyzerAgent

# Patch agent (optional — no hard dependencies)
try:
    from .codebase_patch_agent import CodebasePatchAgent
    PATCH_AGENT_AVAILABLE = True
except ImportError:
    CodebasePatchAgent = None
    PATCH_AGENT_AVAILABLE = False

__all__ = [
    'StaticAnalyzerAgent',
    'CodebasePatchAgent',
    'PATCH_AGENT_AVAILABLE',
]
