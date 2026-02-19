import json
from utils.common.llm_tools import LLMTools
import logging
import sys
import re

# NOTE: Do NOT use logging.basicConfig(force=True) here — it resets the root
# logger and installs a StreamHandler that floods the Streamlit UI console.
# Module-level loggers inherit from the root logger configured by the workers.
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

class CodebaseAnalysisSessionState:
    """
    Maintains the conversational and reasoning state for multiturn orchestration.
    """
    def __init__(self, user_input=None):
        self.user_input = user_input
        self.retrieved_docs = []
        self.records = ""
        self.prompt = None
        self.llm_response = None
        self.formatted_response = None
        # New fields for intent and vectordb query
        self.intent = None
        self.criteria = None
        self.fields_to_extract = None
        self.output_format = None
        self.vectordb_query = None

class CodebaseAnalysisOrchestration:
    def __init__(self, vectordb=None):
        """
        Initialize the orchestration with optional vectordb.

        Args:
            vectordb: Optional VectorDB instance. If None, attempts to
                      initialize from GlobalConfig/EnvConfig automatically.
        """
        if vectordb is None:
            vectordb = self._init_vectordb()
        self.tools = LLMTools(vectordb=vectordb)

    @staticmethod
    def _init_vectordb():
        """Attempt to initialize VectorDB from config. Returns None on failure."""
        try:
            from db.vectordb_wrapper import VectorDB
            try:
                from utils.parsers.global_config_parser import GlobalConfig
                env_config = GlobalConfig()
            except (ImportError, Exception):
                from utils.parsers.env_parser import EnvConfig
                env_config = EnvConfig()
            return VectorDB(env_config)
        except Exception as e:
            logger.warning(f"Could not initialize VectorDB: {e}. Chat retrieval will be unavailable.")
            return None
        
    def flatten_docs(self, retrieved_docs):
        """Always returns a flat list of doc objects, even if retrieved_docs is a list-of-lists."""
        if not retrieved_docs:
            return []
        # If first element is a list (compare case)
        if isinstance(retrieved_docs[0], list):
            flat = []
            for group in retrieved_docs:
                # skip empty lists
                if group:
                    flat.extend(group)
            return flat
        # If already a flat list (retrieve case)
        return retrieved_docs

    def safe_metadata(self, doc):
        """Returns metadata dict whether doc is an object or dict."""
        if hasattr(doc, "metadata"):
            return doc.metadata
        if isinstance(doc, dict):
            return doc.get("metadata", doc)
        return {}

    def format_and_extract_intent_and_query(self, user_query: str) -> dict:
        """
        Sends the user query to the LLM for intent extraction and formatting,
        then generates a custom vector database query string based on the extracted intent.
        Returns a dict with keys:
        - 'intent'
        - 'criteria'
        - 'fields_to_extract'
        - 'output_format'
        - 'vectordb_query'

        Updated to:
        - Consider all health/quality metrics (complexity, dependencies, documentation,
        maintainability, quality, security, testability, etc.).
        - Bias search toward file/definition/code sections where filenames, line numbers,
        and code snippets are available.
        """

        # Step 1: Extract intent using LLM
        try:
            intent_obj = self.tools.extract_intent_from_prompt(user_query)
            logger.info(f"[format_and_extract_intent_and_query] Extracted intent: {intent_obj}")
        except Exception as e:
            logger.error(f"[format_and_extract_intent_and_query] Failed to extract intent: {e}")
            # Fallback: treat as generic retrieve
            intent_obj = {"intent": "retrieve", "criteria": {}}

        # Step 2: Parse intent fields
        intent = intent_obj.get("intent", "retrieve")
        criteria = intent_obj.get("criteria", {}) or {}
        fields_to_extract = intent_obj.get("fields_to_extract", []) or []
        output_format = intent_obj.get("output_format", None)
        entities = intent_obj.get("entities", []) or []

        # --- Metric-related helpers -------------------------------------------------

        # All metric-ish fields we want to bias queries toward
        METRIC_FIELDS = {
            "complexity",
            "dependency_metrics",
            "dependencies",
            "documentation_coverage",
            "maintainability",
            "quality",
            "code_smells",
            "security_issues",
            "security_risks",
            "vulnerabilities",
            "testability",
            "technical_debt",
            "style_compliance",
            "lint_issues",
            "test_coverage",
        }

        def has_metric_fields(fields):
            return any(f in METRIC_FIELDS for f in fields)

        # When the user wants code-level detail (snippets, lines, files)
        def wants_code_detail(fields, text: str) -> bool:
            text_l = text.lower()
            code_fields = {"code", "code_snippets"}
            # heuristic cues in text
            code_cues = [
                "line ", "lines ", "line number", "line numbers",
                "file ", "file:", "filename", "file name",
                "snippet", "snippets",
                "function ", "method ",
            ]
            if any(f in fields for f in code_fields):
                return True
            return any(c in text_l for c in code_cues)

        # Helper to extract module/component names from entities or criteria
        def extract_modules(obj):
            modules = []
            if isinstance(obj, dict) and "module" in obj:
                modules.append(obj["module"])
            elif isinstance(obj, list):
                for ent in obj:
                    if isinstance(ent, dict) and "module" in ent:
                        modules.append(ent["module"])
            return modules

        # Helper to build query for fields
        def build_field_query(fields):
            if not fields:
                return ""
            return " ".join([f"field:{field}" for field in fields])

        # --- Step 3: Build vectordb_query string -----------------------------------

        vectordb_query = None

        # Decide which logical "sections" in your index to hit.
        # Add metric + code/file sections so we can retrieve filenames, line numbers, and snippets.
        # Adjust section names to match your actual indexing scheme.
        base_sections = [
            "section:dependency_graph",
            "section:documentation",
            "section:modularization_plan",
        ]

        # Sections for metrics and code-level info
        metric_sections = [
            "section:metrics",           # e.g., per-file / per-function metric reports
            "section:code_quality",      # lint, smells, style
            "section:test_reports",      # coverage/testability
            "section:security_reports",  # security issues/vulns
        ]

        code_sections = [
            "section:file_index",        # filenames, paths, line ranges
            "section:definitions",       # function/class definitions with line numbers
            "section:code_snippet",      # code fragments
            "section:implementation",
        ]

        fields_are_metrics = has_metric_fields(fields_to_extract)
        needs_code_detail = wants_code_detail(fields_to_extract, user_query)

        # Build section part
        sections = list(base_sections)
        if fields_are_metrics:
            sections.extend(metric_sections)
        if needs_code_detail:
            sections.extend(code_sections)

        # Deduplicate
        sections = sorted(set(sections))
        section_prefix = " OR ".join(sections)

        # Build field query (also include metric fields so the DB can use them)
        field_query = build_field_query(fields_to_extract)

        if intent == "compare":
            modules = extract_modules(entities)
            if modules:
                module_queries = [f"module:{m}" for m in modules]
                vectordb_query = (
                    f"{section_prefix} "
                    + " ".join(module_queries)
                    + (" " + field_query if field_query else "")
                )
            else:
                vectordb_query = section_prefix + (" " + field_query if field_query else "")

        elif intent == "retrieve":
            modules = extract_modules(criteria)

            if modules:
                module_queries = [f"module:{m}" for m in modules]
                vectordb_query = (
                    f"{section_prefix} "
                    + " ".join(module_queries)
                    + (" " + field_query if field_query else "")
                )

            elif fields_to_extract:
                # User is asking explicitly for certain fields/metrics
                vectordb_query = f"{section_prefix} " + field_query

            elif "dependencies" in user_query.lower():
                vectordb_query = f"{section_prefix} dependencies"

            elif "modularization" in user_query.lower():
                vectordb_query = "section:modularization_plan"

            else:
                # fallback to pure semantic search with user query
                vectordb_query = user_query

        elif intent == "aggregate":
            # Aggregate typically across the codebase; bias towards metrics + docs
            vectordb_query = (
                section_prefix
                + (" " + field_query if field_query else "")
            )

        else:
            vectordb_query = user_query  # fallback

        logger.info(
            "[format_and_extract_intent_and_query] Built vectordb_query: %s",
            vectordb_query,
        )

        return {
            "intent": intent,
            "criteria": criteria,
            "fields_to_extract": fields_to_extract,
            "output_format": output_format,
            "vectordb_query": vectordb_query,
        }

    def run_multiturn_chain(self, state: CodebaseAnalysisSessionState, recursion_limit=0):
        """
        Recursive multiturn orchestration. Handles retrieval, prompt, LLM, formatting.
        Escalate for clarification if LLM response is not sufficiently final.
        """
        # Step 0: Extract intent and build vectordb query
        intent_info = self.format_and_extract_intent_and_query(state.user_input)
        state.intent = intent_info.get("intent")
        state.criteria = intent_info.get("criteria")
        state.fields_to_extract = intent_info.get("fields_to_extract")
        state.output_format = intent_info.get("output_format")
        state.vectordb_query = intent_info.get("vectordb_query")
        logger.info(f"Intent info: {intent_info}")

        # Step 1: Retrieve relevant docs using custom vectordb query
        state.retrieved_docs = self.tools.retrieve_relevant_docs(state.vectordb_query)
        flat_docs = self.flatten_docs(state.retrieved_docs)
        state.records = "\n\n====\n\n".join([json.dumps(self.safe_metadata(doc)) for doc in flat_docs])
        logger.info(f"state.records: {state.records}")

        # Step 2: Prepare prompt
        state.prompt = self.tools.update_markdown_prompt(
            self.tools.prompt_file_path,
            state.user_input,
            state.records
        )

        # Step 3: LLM Call
        state.llm_response = self.tools.llm_call(state.prompt)

        # Step 4: Format response
        state.formatted_response = self.tools.format_llm_response(state.llm_response)

        # Step 5: (Optional) Multiturn logic
        if recursion_limit > 0:
            if self.needs_followup(state.formatted_response):
                # Example: Recursively clarify if needed
                clarification_question = self.generate_clarification_question(state.formatted_response)
                state.user_input = clarification_question
                return self.run_multiturn_chain(state, recursion_limit - 1)

        return state

    def needs_followup(self, response):
        """
        Define your own task-completeness heuristics here.
        For demonstration, treat 'clarify' or generic placeholder as signals for follow-up.
        """
        return (
            isinstance(response, str) and
            (
                response.lower().startswith("please clarify") or
                "need more information" in response.lower()
            )
        )

    def generate_clarification_question(self, last_response):
        """
        (Placeholder) Generates a clarification follow-up question.
        Could use an LLM for this in production.
        """
        return "Could you please clarify your request or provide additional context?"