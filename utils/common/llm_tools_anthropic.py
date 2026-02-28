# llm_tools_anthropic.py
"""
Anthropic Claude LLM toolkit for codebase analysis.

Drop-in alternative to llm_tools.py (QGenie).  Exposes the **identical**
public API so every agent that does ``llm_tools.llm_call(prompt)`` keeps
working without modification — just switch the provider in global_config.yaml.

Provides:
- LLM calls via Anthropic Claude Messages API
- Structured intent extraction from natural language prompts
- JSON extraction from LLM responses
- Markdown prompt template rendering
- Vector DB retrieval (optional, injected)
- Tool registry with schema introspection

Usage:
    from utils.common.llm_tools_anthropic import LLMTools

    # Auto-loads config from GlobalConfig (global_config.yaml)
    tools = LLMTools()

    # Override model at runtime
    tools = LLMTools(model="anthropic::claude-sonnet-4-20250514")

    response = tools.llm_call("Analyze this code for security issues...")
"""

from __future__ import annotations

import abc
import json
import re
import uuid
import logging
import time
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass

# Ensure .env is loaded so GlobalConfig can pick up env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class LLMConfig:
    """Configuration for Anthropic Claude LLM provider and behavior."""

    # Model identifier  (format: "anthropic::claude-sonnet-4-20250514")
    raw_model: str = "anthropic::claude-sonnet-4-20250514"
    coding_model: str = "anthropic::claude-sonnet-4-20250514"

    # API keys
    anthropic_api_key: Optional[str] = None

    # Request defaults
    max_tokens: int = 8192
    temperature: float = 0.1
    timeout: int = 120          # seconds
    max_retries: int = 2

    # Intent extraction
    intent_max_tokens: int = 4096
    intent_temperature: float = 0.0

    # Token budget (for prompt truncation)
    max_prompt_tokens: int = 100_000

    # Prompt file path
    chat_prompt_file_path: Optional[str] = None

    @property
    def provider(self) -> str:
        """Return provider name (always anthropic)."""
        return "anthropic"

    @property
    def model(self) -> str:
        """Extract model name from raw_model string."""
        if "::" in self.raw_model:
            return self.raw_model.split("::", 1)[1].strip()
        return self.raw_model

    @property
    def coding_model_name(self) -> str:
        """Extract model name from coding_model string."""
        if "::" in self.coding_model:
            return self.coding_model.split("::", 1)[1].strip()
        return self.coding_model

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """
        Build config from GlobalConfig.
        Maps keys from global_config.yaml to LLMConfig fields.
        """
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            gc = GlobalConfig()
        except ImportError:
            logger.warning("GlobalConfig parser not found. Using defaults.")
            return cls()

        api_key = (
            gc.get("llm.llm_api_key")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")  # backward compat
        )

        return cls(
            raw_model=gc.get("llm.model") or "anthropic::claude-sonnet-4-20250514",
            coding_model=gc.get("llm.coding_model") or "anthropic::claude-sonnet-4-20250514",
            anthropic_api_key=api_key,
            max_tokens=gc.get_int("llm.max_tokens", 8192),
            temperature=gc.get_float("llm.temperature", 0.1),
            timeout=gc.get_int("llm.timeout", 120),
            max_retries=gc.get_int("llm.max_retries", 2),
            intent_max_tokens=gc.get_int("llm.intent_max_tokens", 4096),
            intent_temperature=gc.get_float("llm.intent_temperature", 0.0),
            max_prompt_tokens=gc.get_int("llm.max_prompt_tokens", 100_000),
            chat_prompt_file_path=gc.get("paths.chat_prompt_file_path"),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Exceptions  (identical to llm_tools.py so callers can catch the same types)
# ═══════════════════════════════════════════════════════════════════════════

class LLMError(Exception):
    """Base error for LLM operations."""


class LLMProviderError(LLMError):
    """Error communicating with the LLM provider."""


class LLMResponseError(LLMError):
    """Error parsing or validating an LLM response."""


class IntentExtractionError(LLMError):
    """Failed to extract structured intent from a prompt."""


class ProviderNotAvailableError(LLMError):
    """Required provider SDK is not installed or configured."""


# ═══════════════════════════════════════════════════════════════════════════
# Provider Abstraction Layer
# ═══════════════════════════════════════════════════════════════════════════

class BaseLLMProvider(abc.ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abc.abstractmethod
    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Send a completion request and return the text response."""
        ...

    def complete_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        tool_choice: Optional[Dict] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a completion with tool definitions (function calling).
        Default fallback: just do a regular completion.
        """
        text = self.complete(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
        )
        return {"text": text, "tool_calls": [], "stop_reason": "end_turn"}


# ---------------------------------------------------------------------------
# Anthropic Claude Provider
# ---------------------------------------------------------------------------

# Max output tokens per model family (conservative defaults)
_MODEL_MAX_OUTPUT = {
    "claude-sonnet": 64000,
    "claude-haiku": 64000,
    "claude-opus": 32000,
}
_DEFAULT_MAX_OUTPUT = 16384


def _clamp_max_tokens(model: str, requested: int) -> int:
    """Clamp max_tokens to the model's output limit."""
    limit = _DEFAULT_MAX_OUTPUT
    model_lower = model.lower()
    for prefix, cap in _MODEL_MAX_OUTPUT.items():
        if prefix in model_lower:
            limit = cap
            break
    return min(requested, limit)


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude model provider.
    Uses the official `anthropic` Python SDK for completions.
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._client = None

        # Validation: Warn if API key is missing
        if not self.config.anthropic_api_key:
            logger.warning(
                "Anthropic API Key is missing in LLMConfig. "
                "Ensure ANTHROPIC_API_KEY is set in environment or global_config.yaml."
            )

    @property
    def client(self):
        """Lazy-initialise the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ProviderNotAvailableError(
                    "The 'anthropic' package is required for Anthropic Claude models. "
                    "Install it with: pip install anthropic"
                )

            init_kwargs: Dict[str, Any] = {}
            if self.config.anthropic_api_key:
                init_kwargs["api_key"] = self.config.anthropic_api_key
            if self.config.timeout:
                init_kwargs["timeout"] = float(self.config.timeout)
            if self.config.max_retries:
                init_kwargs["max_retries"] = self.config.max_retries

            self._client = anthropic.Anthropic(**init_kwargs)
        return self._client

    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Call Anthropic Claude Messages API."""
        # Separate system message if embedded in messages list
        api_messages = []
        resolved_system = system
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                # Anthropic expects system as a top-level param, not in messages
                if resolved_system is None:
                    resolved_system = content
                else:
                    resolved_system = resolved_system + "\n\n" + content
            else:
                api_messages.append({"role": role, "content": content})

        # Ensure at least one user message
        if not api_messages:
            api_messages = [{"role": "user", "content": ""}]

        raw_max = max_tokens or self.config.max_tokens
        safe_max = _clamp_max_tokens(self.config.model, raw_max)

        create_kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": safe_max,
            "messages": api_messages,
        }
        if resolved_system:
            create_kwargs["system"] = resolved_system
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        else:
            create_kwargs["temperature"] = self.config.temperature

        try:
            start = time.monotonic()
            response = self.client.messages.create(**create_kwargs)
            elapsed = time.monotonic() - start

            logger.debug(
                "Anthropic API: model=%s elapsed=%.2fs input_tokens=%d output_tokens=%d",
                self.config.model,
                elapsed,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            return response.content[0].text

        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            raise LLMProviderError(f"Anthropic API call failed: {e}") from e

    def complete_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        tool_choice: Optional[Dict] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call Anthropic Claude Messages API with tool use."""
        api_messages = []
        resolved_system = system
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                if resolved_system is None:
                    resolved_system = content
                else:
                    resolved_system = resolved_system + "\n\n" + content
            else:
                api_messages.append({"role": role, "content": content})

        if not api_messages:
            api_messages = [{"role": "user", "content": ""}]

        create_kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "messages": api_messages,
            "tools": tools,
        }
        if resolved_system:
            create_kwargs["system"] = resolved_system
        if tool_choice:
            create_kwargs["tool_choice"] = tool_choice

        try:
            response = self.client.messages.create(**create_kwargs)

            text_parts = []
            tool_calls = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            return {
                "text": "\n".join(text_parts),
                "tool_calls": tool_calls,
                "stop_reason": response.stop_reason,
            }

        except Exception as e:
            logger.error("Anthropic API error (tools): %s", e)
            raise LLMProviderError(f"Anthropic API call with tools failed: {e}") from e


def create_provider(config: LLMConfig) -> BaseLLMProvider:
    """Factory function: create the Anthropic provider."""
    logger.info("LLM provider: Anthropic, model: %s", config.model)
    return AnthropicProvider(config)


# ═══════════════════════════════════════════════════════════════════════════
# Main LLMTools Class
# ═══════════════════════════════════════════════════════════════════════════

class LLMTools:
    """
    Anthropic Claude LLM toolkit for codebase analysis.

    API-compatible with the QGenie LLMTools in llm_tools.py.
    Every public method has the same signature and return type.

    Features:
    - Single-provider routing (Anthropic Claude)
    - Structured intent extraction from natural language
    - JSON response parsing with fallback
    - Markdown prompt template rendering
    - Vector DB retrieval (optional, injected)
    - Pluggable prompt builder and keywords

    Usage:
        tools = LLMTools()                                          # uses GlobalConfig
        tools = LLMTools(model="anthropic::claude-sonnet-4-20250514") # override
        response = tools.llm_call("Analyze this code...")
    """

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        model: Optional[str] = None,
        vectordb=None,
        intent_prompt_builder: Optional[Callable[[str], str]] = None,
        full_report_keywords: Optional[List[str]] = None,
    ):
        """
        Initialize LLM tools.

        Args:
            config: Explicit LLMConfig. If None, auto-loads from GlobalConfig.
            model: Override model string (e.g., "anthropic::claude-sonnet-4-20250514").
            vectordb: Optional VectorDB instance for retrieval methods.
            intent_prompt_builder: Pluggable function(user_input) -> system_prompt.
            full_report_keywords: Keywords for is_full_report_request().
        """
        # 1. Load Configuration
        if config:
            self.config = config
        else:
            self.config = LLMConfig.from_env()

        # 2. Apply Runtime Overrides
        if model:
            self.config.raw_model = model
            self.config.coding_model = model

        # Create the provider
        self.provider = create_provider(self.config)

        # Optional vector DB
        self.vectordb = vectordb

        # Pluggable prompt builder
        self._intent_prompt_builder = intent_prompt_builder
        if self._intent_prompt_builder is None:
            try:
                from utils.prompts.prompts import get_intent_extraction_prompt
                self._intent_prompt_builder = get_intent_extraction_prompt
            except ImportError:
                self._intent_prompt_builder = self._default_intent_prompt

        # Full report keywords
        self._full_report_keywords = full_report_keywords
        if self._full_report_keywords is None:
            try:
                from utils.prompts.prompts import FULL_REPORT_KEYWORDS
                self._full_report_keywords = FULL_REPORT_KEYWORDS
            except ImportError:
                self._full_report_keywords = [
                    "all modules", "all", "trend", "all records",
                    "summary", "entire codebase",
                ]

        # Resolve prompt file path
        self.prompt_file_path = None
        if self.config.chat_prompt_file_path:
            try:
                self.prompt_file_path = self.resolve_relative_path(
                    self.config.chat_prompt_file_path
                )
            except Exception:
                pass

        self.logger = logger

    @classmethod
    def from_env(cls, **kwargs) -> "LLMTools":
        """Factory: build LLMTools from environment configuration."""
        return cls(config=LLMConfig.from_env(), **kwargs)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_repo_root() -> Path:
        return Path(__file__).resolve().parent.parent.parent

    @classmethod
    def resolve_relative_path(cls, relative_path: Union[str, Path]) -> Path:
        if relative_path is None:
            raise ValueError("Path is not set.")
        p = Path(relative_path)
        if p.is_absolute():
            return p
        return cls.get_repo_root() / p

    # ------------------------------------------------------------------
    # Core LLM Calls
    # ------------------------------------------------------------------

    def llm_call(self, prompt, model=None):
        """
        Make a simple LLM call and return response text.

        Signature matches llm_tools.py exactly so agents work unchanged.

        Args:
            prompt: The prompt string to send.
            model: Optional model override (e.g. "anthropic::claude-sonnet-4-20250514").

        Returns:
            Response text string.
        """
        # Determine which model to use
        target_model = model if model else self.config.coding_model_name

        # Resolve bare model name (strip provider prefix if present)
        if "::" in target_model:
            target_model = target_model.split("::", 1)[1].strip()

        api_key = self.config.anthropic_api_key

        try:
            import anthropic
        except ImportError:
            raise ProviderNotAvailableError(
                "The 'anthropic' package is required. Install with: pip install anthropic"
            )

        try:
            print(f"Initializing Anthropic Claude with MODEL: {target_model}")

            client_kwargs: Dict[str, Any] = {}
            if api_key:
                client_kwargs["api_key"] = api_key
            if self.config.timeout:
                client_kwargs["timeout"] = float(self.config.timeout)
            if self.config.max_retries:
                client_kwargs["max_retries"] = self.config.max_retries

            client = anthropic.Anthropic(**client_kwargs)

            safe_max = _clamp_max_tokens(target_model, self.config.max_tokens)

            response = client.messages.create(
                model=target_model,
                max_tokens=safe_max,
                temperature=self.config.temperature,
                messages=[{"role": "user", "content": prompt}],
            )

            return response.content[0].text

        except Exception as e:
            logger.error(f"LLM invocation failed: {e}")
            return f"LLM invocation failed: {e}"

    # ------------------------------------------------------------------
    # Intent Extraction
    # ------------------------------------------------------------------

    def extract_intent_from_prompt(self, user_input_prompt: str) -> Dict[str, Any]:
        """
        Parse a natural language prompt into a structured intent object.

        Returns:
            Parsed intent dict with: intent, criteria, entities,
            fields_to_extract, output_format.
        """
        system_prompt = self._intent_prompt_builder(user_input_prompt)

        # Note: llm_call() only accepts (prompt, model) — intent-specific
        # max_tokens / temperature are handled by the provider config.
        raw_response = self.llm_call(prompt=system_prompt)

        json_str = self.extract_json_from_llm_response(raw_response)

        try:
            intent_obj = json.loads(json_str)
            if not isinstance(intent_obj, dict):
                raise ValueError(f"Expected dict, got {type(intent_obj)}")

            if "intent" not in intent_obj:
                intent_obj["intent"] = "retrieve"
            if intent_obj["intent"] not in ("retrieve", "compare", "aggregate"):
                intent_obj["intent"] = "retrieve"

            return intent_obj
        except json.JSONDecodeError as e:
            logger.error("Intent JSON parse failed: %s | raw: %s", e, raw_response[:500])
            raise IntentExtractionError(f"Failed to parse intent JSON: {e}") from e
        except Exception as e:
            logger.error("Intent extraction failed: %s", e)
            raise IntentExtractionError(f"Intent extraction failed: {e}") from e

    @staticmethod
    def _default_intent_prompt(user_input: str) -> str:
        """Minimal fallback intent prompt when prompts.py is unavailable."""
        return (
            "You are an expert codebase analysis assistant.\n\n"
            "Parse the user's query and return a JSON object with:\n"
            '- "intent": "retrieve" | "compare" | "aggregate"\n'
            '- "criteria": filter object (or {} for all)\n'
            '- "entities": list of entity descriptors (for compare)\n'
            '- "fields_to_extract": list of requested info types\n'
            '- "output_format": "summary" (default) | "table" | "list" | "json"\n\n'
            "Only return the JSON object.\n\n"
            f"User prompt: {user_input}\n"
        )

    # ------------------------------------------------------------------
    # Response Parsing Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def extract_json_from_llm_response(response: str) -> str:
        """
        Extract a JSON object from an LLM response that may be wrapped
        in markdown code fences or contain surrounding text.
        """
        if not isinstance(response, str):
            return str(response)

        response = response.strip()

        # Try markdown code fence
        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", response)
        if match:
            return match.group(1).strip()

        # Try to find a JSON object (first { to matching })
        brace_start = response.find("{")
        brace_end = response.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            return response[brace_start:brace_end + 1]

        # Try to find a JSON array
        bracket_start = response.find("[")
        bracket_end = response.rfind("]")
        if bracket_start != -1 and bracket_end > bracket_start:
            return response[bracket_start:bracket_end + 1]

        return response

    @staticmethod
    def format_llm_response(agent_response: Any) -> str:
        """Normalize various LLM response formats into a plain string."""
        if agent_response is None:
            return "No response."

        if isinstance(agent_response, str):
            return agent_response

        # Handle objects with .content (LangChain message / Anthropic response)
        if hasattr(agent_response, "content"):
            content = agent_response.content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    elif isinstance(block, dict) and "text" in block:
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                return "\n".join(parts) if parts else "No text content."
            return str(content)

        # Handle list of messages
        if isinstance(agent_response, list) and agent_response:
            for msg in reversed(agent_response):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    return msg.get("content", "No content.")
                if hasattr(msg, "role") and msg.role == "assistant":
                    return getattr(msg, "content", "No content.")
            last = agent_response[-1]
            if isinstance(last, dict):
                return last.get("content", str(last))
            if hasattr(last, "content"):
                return str(last.content)
            return str(last)

        return str(agent_response)

    # ------------------------------------------------------------------
    # Prompt Template Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def update_markdown_prompt(
        md_filepath: Union[str, Path],
        input_str: str,
        records: Any,
        **extra_vars,
    ) -> Optional[str]:
        """Load a markdown prompt template and fill in variables."""
        try:
            path = Path(md_filepath)
            content = path.read_text(encoding="utf-8").strip()
            return content.format(
                records=records,
                input_str=input_str,
                **extra_vars,
            )
        except FileNotFoundError:
            logger.error("Prompt template not found: %s", md_filepath)
            return None
        except KeyError as e:
            logger.error("Missing template variable %s in %s", e, md_filepath)
            return None
        except Exception as e:
            logger.error("Error rendering prompt template %s: %s", md_filepath, e)
            return None

    # ------------------------------------------------------------------
    # Retrieval (optional — only active if vectordb is injected)
    # ------------------------------------------------------------------

    def retrieve_relevant_docs(self, input_str: str, top_k: int = 250) -> list:
        """
        Hybrid semantic/metadata retrieval using the injected VectorDB.
        Returns empty list if no vectordb is configured.
        """
        if self.vectordb is None:
            logger.warning("retrieve_relevant_docs called but no vectordb configured.")
            return []

        try:
            intent_obj = self.extract_intent_from_prompt(input_str)
        except Exception as e:
            logger.error("Intent extraction failed in retrieve: %s", e)
            intent_obj = {"intent": "retrieve", "criteria": {}}

        # Handle compare intent (multi-entity)
        if intent_obj.get("intent") == "compare" and "entities" in intent_obj:
            results = []
            for entity in intent_obj["entities"]:
                criteria = {k: v for k, v in entity.items() if k != "fields_to_extract"}
                query_str = " ".join(f"{k} {v}" for k, v in criteria.items())
                matched = self._semantic_and_metadata_search(query_str, criteria, top_k)
                results.append(matched)
            return results

        # Single-entity retrieve/aggregate
        criteria = intent_obj.get("criteria", {})
        return self._semantic_and_metadata_search(input_str, criteria, top_k)

    def _semantic_and_metadata_search(
        self, query_str: str, criteria: dict, top_k: int = 250,
    ) -> list:
        """Semantic search with metadata filtering."""
        docs = self.vectordb.retrieve(query_str, k=top_k)
        results = []
        for doc in docs:
            meta = getattr(doc, "metadata", None)
            if not meta:
                continue
            matches = True
            for k, v in criteria.items():
                meta_val = meta.get(k) if isinstance(meta, dict) else getattr(meta, k, None)
                data_val = getattr(doc, "data", {}).get(k) if hasattr(doc, "data") else None
                if meta_val == v or data_val == v:
                    continue
                matches = False
                break
            if matches:
                results.append(doc)
        return results

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def is_full_report_request(self, user_input: str) -> bool:
        """Check if the user is requesting a full/all-modules report."""
        lower = user_input.lower()
        return any(kw in lower for kw in self._full_report_keywords)

    @staticmethod
    def is_uuid(value: str) -> bool:
        """Check if a string is a valid UUID."""
        try:
            uuid.UUID(str(value))
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def count_tokens_approx(text: str) -> int:
        """Approximate token count (~4 chars/token heuristic)."""
        return len(text) // 4

    def truncate_to_token_budget(self, text: str, max_tokens: Optional[int] = None) -> str:
        """Truncate text to fit within a token budget."""
        budget = max_tokens or self.config.max_prompt_tokens
        current = self.count_tokens_approx(text)
        if current <= budget:
            return text
        char_limit = budget * 4
        return text[:char_limit] + "\n\n[... truncated due to token limit ...]"

    def metadata_filtering(self, all_records: list, **criteria) -> list:
        """Filter records matching all given metadata/data criteria."""
        def match(rec):
            for key, val in criteria.items():
                meta_val = rec.get("metadata", {}).get(key)
                data_val = rec.get("data", {}).get(key)
                if meta_val == val or data_val == val:
                    continue
                return False
            return True
        return [rec for rec in all_records if match(rec)]

    # ------------------------------------------------------------------
    # Tool Registry
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return compatible tool definitions for this toolkit."""
        return [
            {
                "name": "analyze_code",
                "description": "Analyze code for quality, security, complexity.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Source code to analyze."},
                        "analysis_type": {
                            "type": "string",
                            "enum": ["quality", "security", "complexity", "dependencies", "architecture", "full"],
                        },
                        "language": {"type": "string", "description": "Programming language."},
                    },
                    "required": ["code"],
                },
            },
            {
                "name": "extract_intent",
                "description": "Extract structured intent from a natural language query.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language query."},
                    },
                    "required": ["query"],
                },
            },
        ]

    def get_tools_map(self, names: Optional[List[str]] = None) -> Dict[str, Any]:
        """Build a tool map for agent/function-calling integration."""
        tool_mapping = {
            "retrieve_relevant_docs": self.retrieve_relevant_docs,
            "update_markdown_prompt": self.update_markdown_prompt,
            "llm_call": self.llm_call,
            "format_llm_response": self.format_llm_response,
        }

        if names is None:
            names = list(tool_mapping.keys())

        tools_map = {}
        all_tools = []
        for name in names:
            if name not in tool_mapping:
                logger.warning("Unknown tool: %s", name)
                continue
            tool = tool_mapping[name]
            tools_map[name] = {"call": tool}
            all_tools.append(tool)

        tools_map["__all__"] = {"call": all_tools}
        return tools_map

    def get_all_available_tools(self) -> List[str]:
        """Return names of all available tool methods."""
        return [
            "llm_call", "llm_call_with_messages", "llm_call_with_tools",
            "llm_call_structured", "extract_intent_from_prompt",
            "retrieve_relevant_docs", "update_markdown_prompt",
            "format_llm_response", "truncate_to_token_budget",
        ]

    # ------------------------------------------------------------------
    # Provider Info
    # ------------------------------------------------------------------

    def get_provider_info(self) -> Dict[str, str]:
        """Return current provider and model information."""
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "raw_model": self.config.raw_model,
            "provider_class": type(self.provider).__name__,
        }

    def switch_model(self, model: str) -> None:
        """Switch the active model at runtime."""
        self.config.raw_model = model
        self.provider = create_provider(self.config)
        logger.info("Switched to: %s", self.config.model)

    def __repr__(self) -> str:
        return (
            f"LLMTools(provider='{self.config.provider}', "
            f"model='{self.config.model}')"
        )
