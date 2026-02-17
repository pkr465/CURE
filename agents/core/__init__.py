"""
Core C/C++ analysis components
"""

from .file_processor import FileProcessor
from .metrics_calculator import MetricsCalculator

__all__ = ['FileProcessor', 'DependencyAnalyzer', 'MetricsCalculator']