"""
Prompt templates for C/C++ analysis.

PromptTemplates provides structured prompts for all analysis stages.
For LLM provider access, use utils.common.llm_tools.LLMTools.
"""

from .prompts import PromptTemplates

__all__ = ['PromptTemplates']
