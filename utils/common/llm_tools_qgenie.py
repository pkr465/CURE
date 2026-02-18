# llm_tools_qgenie.py
"""
QGenie-specific LLM toolkit for codebase analysis.

Provides:
- LLM calls via QGenieProvider
- Structured intent extraction from natural language prompts
- JSON extraction from LLM responses
- Markdown prompt template rendering
- Vector DB retrieval (optional, injected)
- Tool registry with schema introspection

Usage (via router — preferred):
    from utils.common.llm_tools import LLMTools   # auto-selects provider

Usage (direct):
    from utils.common.llm_tools_qgenie import LLMTools

    tools = LLMTools(model="qgenie::qwen2.5-14b-1m")
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
    """Configuration for QGenie LLM provider and behavior."""

    # Model identifier
    raw_model: str = "qgenie::qwen2.5-14b-1m"
    coding_model: str = "qgenie::qwen2.5-14b-1m"

    # API keys (QGenie specific)
    qgenie_api_key: Optional[str] = None
    
    # Endpoints
    qgenie_endpoint: Optional[str] = None

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
        """Return provider name (always qgenie)."""
        return "qgenie"

    @property
    def model(self) -> str:
        """Extract model name from raw_model string."""
        if "::" in self.raw_model:
            return self.raw_model.split("::", 1)[1].strip()
        return self.raw_model

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """
        Build config from GlobalConfig.
        Maps keys from global_config.yaml to LLMConfig fields, removing '::' prefixes.
        """
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            gc = GlobalConfig()
        except ImportError:
            logger.warning("GlobalConfig parser not found. Using defaults.")
            return cls()

        # --- Helper: qgenie::anthropic::claude -> anthropic::claude ---
        def clean_name(name: str) -> str:
            if name and "::" in name:
                # split(separator, maxsplit) -> returns [provider, remainder]
                return name.split("::", 1)[1].strip() 
            return name

        # 1. Fetch Raw Strings
        api_key = gc.get("llm.qgenie_api_key") or os.environ.get("QGENIE_API_KEY")

        # 3. Pass CLEANED strings to constructor
        return cls(
            raw_model=gc.get("llm.model") or "qgenie::qwen2.5-14b-1m",            
            coding_model=gc.get("llm.coding_model") or "anthropic::claude-4-5-sonnet",
            qgenie_api_key=api_key,
            qgenie_endpoint=gc.get("llm.chat_endpoint"),
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
# Exceptions
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
    """
    Abstract base class for LLM providers.
    """

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
# QGenie Provider
# ---------------------------------------------------------------------------

class QGenieProvider(BaseLLMProvider):
    """
    QGenie model provider.
    Uses `qgenie.integrations.langchain.QGenieChat` for completions.
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._model = None
        
        # Validation: Warn if API key is missing
        if not self.config.qgenie_api_key:
            logger.warning(
                "QGenie API Key is missing in LLMConfig. "
                "Ensure QGENIE_API_KEY is set in environment or global_config.yaml."
            )

    @property
    def model_instance(self):
        if self._model is None:
            try:
                from qgenie.integrations.langchain import QGenieChat
            except ImportError:
                raise ProviderNotAvailableError(
                    "The 'qgenie' package is required for QGenie models. "
                    "Install it with: pip install qgenie"
                )
            
            init_kwargs = {
                "model": self.config.model,
                "timeout": self.config.timeout,
            }

            # Only pass api_key if it exists; otherwise let QGenie SDK resolve it
            if self.config.qgenie_api_key:
                init_kwargs["api_key"] = self.config.qgenie_api_key
            
            # Pass endpoint if configured
            if self.config.qgenie_endpoint:
                init_kwargs["endpoint"] = self.config.qgenie_endpoint
                
            self._model = QGenieChat(**init_kwargs)
            
        return self._model

    def complete(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except ImportError:
            raise ProviderNotAvailableError(
                "langchain_core is required for QGenie provider. "
                "Install it with: pip install langchain-core"
            )

        lc_messages = []
        if system:
            lc_messages.append(SystemMessage(content=system))

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "system":
                lc_messages.append(SystemMessage(content=content))

        try:
            start = time.monotonic()
            
            # Invoke with specific params
            result = self.model_instance.invoke(
                lc_messages,
                max_tokens=max_tokens or self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                repetition_penalty=1.1,
                top_k=50,
                top_p=0.95,
            )
            elapsed = time.monotonic() - start

            logger.debug(
                "QGenie API: model=%s elapsed=%.2fs",
                self.config.model, elapsed,
            )
            return result.content

        except Exception as e:
            logger.error("QGenie API error: %s", e)
            raise LLMProviderError(f"QGenie API call failed: {e}") from e


def create_provider(config: LLMConfig) -> BaseLLMProvider:
    """
    Factory function: create the QGenie provider.
    """
    logger.info("LLM provider: QGenie, model: %s", config.model)
    return QGenieProvider(config)


# ═══════════════════════════════════════════════════════════════════════════
# Main LLMTools Class
# ═══════════════════════════════════════════════════════════════════════════

class LLMTools:
    """
    QGenie-specific LLM toolkit for codebase analysis.

    Features:
    - Single-provider routing (QGenie)
    - Structured intent extraction from natural language
    - JSON response parsing with fallback
    - Markdown prompt template rendering
    - Vector DB retrieval (optional, injected)
    - Pluggable prompt builder and keywords

    Usage:
        tools = LLMTools()                                    # uses GlobalConfig defaults
        tools = LLMTools(model="qgenie::qwen2.5-14b-1m")      # Override model

        response = tools.llm_call("Analyze this code...")
        intent   = tools.extract_intent_from_prompt("Compare A and B")
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
            model: Override model string (e.g., "qgenie::qwen2.5-14b-1m").
            vectordb: Optional VectorDB instance for retrieval methods.
            intent_prompt_builder: Pluggable function(user_input) -> system_prompt.
            full_report_keywords: Keywords for is_full_report_request().
        """
        # 1. Load Configuration
        # Default: Load from GlobalConfig via from_env()
        if config:
            self.config = config
        else:
            self.config = LLMConfig.from_env()

        # 2. Apply Runtime Overrides
        # Only override if explicitly passed by caller; otherwise respect config file
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
        from qgenie.integrations.langchain import QGenieChat
        from langchain_core.messages import HumanMessage
        
        # 1. Determine which model name to use
        # If 'model' argument is provided, use it; otherwise fallback to config.coding_model
        target_model = model if model else self.config.coding_model
        api_key = self.config.qgenie_api_key

        try:
            print(f"Initializing QGenieChat with MODEL: {target_model}")

            # 2. Initialize the Client
            # Renamed variable to 'chat_client' to avoid conflict with 'model' string
            chat_client = QGenieChat(
                model=target_model,
                api_key=api_key,
                timeout=15000
            )

            # 3. Prepare and Invoke
            messages = [HumanMessage(content=prompt)]
            
            result = chat_client.invoke(
                messages,
                max_tokens=15000,
                repetition_penalty=1.1,
                temperature=0.1,
                top_k=50,
                top_p=0.95,
            )
            
            return result.content

        except Exception as e:
            logger.error(f"LLM invocation failed: {e}")
            # Depending on your use case, you might want to 'raise e' here
            # instead of returning a string error message.
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

        # Handle objects with .content (LangChain message)
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
        """
        Build a tool map for agent/function-calling integration.
        """
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
        """
        Switch the active model at runtime.
        """
        self.config.raw_model = model
        self.provider = create_provider(self.config)
        logger.info("Switched to: %s", self.config.model)

    def __repr__(self) -> str:
        return (
            f"LLMTools(provider='{self.config.provider}', "
            f"model='{self.config.model}')"
        )