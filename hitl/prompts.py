"""
CURE — Codebase Update & Refactor Engine
HITL Prompt Templates

Agent-specific prompt prefixes that inject RAG context from past human
feedback into LLM calls.  Each prefix is formatted with ``{hitl_context}``
at call time.
"""


class HITLPromptTemplates:
    """Prompt templates for RAG-augmented LLM calls."""

    # ── CodebaseLLMAgent ─────────────────────────────────────────────────
    LLM_AGENT_PREFIX = """\
# Human-in-the-Loop Context

The following context comes from past human reviews and documented
constraints.  Use it to improve your analysis:

{hitl_context}

**Instructions based on context above:**
1. Do NOT flag issues that past reviewers marked as SKIP or FALSE POSITIVE.
2. When a constraint rule applies (e.g., "IGNORE standard remediation"),
   follow the documented LLM Action instead of the standard fix.
3. Match severity levels with past similar decisions when possible.
4. Include any human remediation notes in your recommendations.

---

"""

    # ── StaticAnalyzerAgent ──────────────────────────────────────────────
    STATIC_ANALYZER_PREFIX = """\
# Human-in-the-Loop Feedback Context

Before aggregating static analysis results, consider this feedback from
past human reviews:

{hitl_context}

**Filtering rules:**
- Suppress issues that were marked FALSE POSITIVE or SKIP in past reviews.
- Weight remaining issues by human-assigned priority when available.
- Apply constraint rules to adjust severity or remediation suggestions.

---

"""

    # ── CodebaseFixerAgent ───────────────────────────────────────────────
    FIXER_AGENT_PREFIX = """\
# Human-in-the-Loop Constraints

Before generating fixes, review these constraints and past decisions:

{hitl_context}

**Fix strategy:**
1. SKIP issues flagged as "SKIP" by human reviewers (return code unchanged).
2. For FIX_WITH_CONSTRAINTS issues, apply the documented constraints
   EXACTLY — do not use the standard remediation.
3. When a constraint says "IGNORE" or "RETAIN", preserve the original
   code and add a safety comment if appropriate.
4. Honour specific remediation notes from past reviews.
5. Add ``// INTENTIONAL: <reason>`` comments when suppressing standard rules.

---

"""

    # ── Generic fallback ─────────────────────────────────────────────────
    GENERIC_PREFIX = """\
# Human Feedback Context

{hitl_context}

Use the above context to inform your analysis and decisions.

---

"""

    # ── Template map ─────────────────────────────────────────────────────
    _TEMPLATES = {
        "llm_agent": LLM_AGENT_PREFIX,
        "static_analyzer": STATIC_ANALYZER_PREFIX,
        "fixer_agent": FIXER_AGENT_PREFIX,
    }

    @classmethod
    def inject_hitl_context(
        cls,
        original_prompt: str,
        hitl_context: str,
        agent_type: str = "llm_agent",
    ) -> str:
        """Prepend the appropriate HITL prefix to an agent prompt.

        Args:
            original_prompt: The agent's original LLM prompt.
            hitl_context: Formatted context string from
                :meth:`HITLContext.get_augmented_context`.
            agent_type: One of ``"llm_agent"``, ``"static_analyzer"``,
                ``"fixer_agent"``.

        Returns:
            The original prompt with the HITL prefix prepended.
        """
        template = cls._TEMPLATES.get(agent_type, cls.GENERIC_PREFIX)
        prefix = template.format(hitl_context=hitl_context)
        return prefix + original_prompt
