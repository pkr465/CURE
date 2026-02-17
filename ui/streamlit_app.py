"""
streamlit_app.py

CURE — Codebase Update & Refactor Engine
Streamlit dashboard for interactive codebase health exploration,
LLM-powered chat, and static analysis metrics visualization.

Author: Pavan R
"""

import os
import re
import io
import json
import logging

import streamlit as st
import pandas as pd

# --- Agent imports (graceful fallback) ---
try:
    from agents.codebase_analysis_chat_agent import (
        CodebaseAnalysisSessionState,
        CodebaseAnalysisOrchestration,
    )
    CHAT_AGENT_AVAILABLE = True
except ImportError:
    CHAT_AGENT_AVAILABLE = False

# --- Config imports ---
try:
    from utils.parsers.global_config_parser import GlobalConfig
    _gc = GlobalConfig()
    STREAMLIT_MODEL = _gc.get("llm.streamlit_model") or "qgenie::qwen2.5-14b-1m"
except Exception:
    from utils.parsers.env_parser import EnvConfig
    _ec = EnvConfig()
    STREAMLIT_MODEL = _ec.get("STREAMLIT_MODEL") or "qgenie::qwen2.5-14b-1m"

import ui.streamlit_tools as st_tools

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
APP_TITLE = "CURE — Codebase Update & Refactor Engine"
APP_ICON = "🔬"
PLACEHOLDER = "🔬 _Analyzing..._"

# ── Logo paths ───────────────────────────────────────────────────────────────
_UI_DIR = os.path.dirname(__file__)
LOGO_MAIN = os.path.join(_UI_DIR, "qualcomm_logo.png")
LOGO_SIDEBAR = os.path.join(_UI_DIR, "qualcomm_logo_2.png")


# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state defaults ───────────────────────────────────────────────────
_DEFAULTS = {
    "chat_history": [],
    "chat_summary": "",
    "all_feedback": [],
    "feedback_mode": False,
    "debug_mode": False,
    "active_page": "Chat",
}
for key, default in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ── Agent cache ──────────────────────────────────────────────────────────────
@st.cache_resource
def _get_orchestrator():
    if CHAT_AGENT_AVAILABLE:
        return CodebaseAnalysisOrchestration()
    return None


orchestrator = _get_orchestrator()


# ═══════════════════════════════════════════════════════════════════════════════
#  Helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_content(answer):
    """Pull plain-text content out of various LLM response shapes."""
    if isinstance(answer, dict) and "content" in answer:
        return answer["content"]
    if isinstance(answer, str):
        stripped = answer.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict) and "content" in parsed:
                    return parsed["content"]
            except (json.JSONDecodeError, ValueError):
                pass
        return answer
    return str(answer)


def _render_markdown_with_tables(md_text: str):
    """Render markdown, and additionally show any embedded tables as DataFrames."""
    st.markdown(md_text, unsafe_allow_html=True)
    table_pattern = r"(\|[^\n]+\|\n(?:\|[:\-]+\|)+\n(?:\|.*\|\n?)+)"
    for match in re.finditer(table_pattern, md_text):
        try:
            df = pd.read_csv(io.StringIO(match.group(0)), sep="|", engine="python")
            df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
            st.dataframe(df, use_container_width=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Pages
# ═══════════════════════════════════════════════════════════════════════════════

def page_chat():
    """Interactive codebase health chat powered by LLM orchestration."""
    st.markdown(
        "<h2 style='text-align:center; margin-top:-10px;'>"
        "Codebase Health Chat</h2>",
        unsafe_allow_html=True,
    )

    if not CHAT_AGENT_AVAILABLE or orchestrator is None:
        st.error(
            "Chat agent is not available. Ensure `agents/codebase_analysis_chat_agent.py` "
            "is installed and that the vector DB has been populated via `main.py --enable-vector-db`."
        )
        return

    # Welcome message
    with st.chat_message("assistant", avatar=APP_ICON):
        st.markdown(
            "<b>Welcome to <span style='color:#00BCD4;'>CURE</span> "
            "Codebase Health Chat!</b><br>"
            "Ask about dependencies, complexity, security, documentation, "
            "maintainability, test coverage, and refactoring recommendations.",
            unsafe_allow_html=True,
        )

    st_tools.feedback_info_if_enabled()

    # Sample queries
    with st.expander("Example questions you can ask"):
        st.markdown(
            "- **Module deep-dive**: _Show all details about the auth module — "
            "dependencies, security risks, test coverage, and documentation gaps._\n"
            "- **Overall health**: _Summarize the codebase health across all dimensions. "
            "Highlight the top 3 issues I should fix first._\n"
            "- **Security audit**: _List all high-severity security findings with file "
            "names and line numbers._\n"
            "- **Dead code**: _Which functions are unreachable from any entry point?_\n"
            "- **Complexity hotspots**: _Show functions with cyclomatic complexity above 25._"
        )

    # Render existing history
    if st.session_state.chat_summary:
        st.info("Earlier conversation summary: " + st.session_state.chat_summary)
        st.download_button(
            "Download Summary",
            st.session_state.chat_summary,
            file_name="cure_chat_summary.txt",
        )

    for idx, (speaker, text) in enumerate(st.session_state.chat_history):
        role = "user" if speaker == "You" else "assistant"
        avatar = "🧑" if role == "user" else APP_ICON
        with st.chat_message(role, avatar=avatar):
            if role == "assistant":
                _render_markdown_with_tables(_extract_content(text))
            else:
                st.markdown(text)

            # Feedback widget for assistant messages
            if role == "assistant":
                user_msg = ""
                if idx > 0 and st.session_state.chat_history[idx - 1][0] == "You":
                    user_msg = st.session_state.chat_history[idx - 1][1]
                feedback = st_tools.feedback_widget(idx, user_msg, text)
                if feedback:
                    st.session_state["all_feedback"].append(feedback)

    # Summarize old turns to keep context manageable
    max_turns = 25
    history = st.session_state.chat_history
    if len(history) > max_turns:
        old_messages = history[:-max_turns]
        st.session_state.chat_summary = st_tools.summarize_chat(
            old_messages, st.session_state.chat_summary
        )
        st.session_state.chat_history = history[-max_turns:]

    # New user input
    user_input = st.chat_input("Ask about your codebase's health and metrics:")
    if user_input:
        st.session_state.chat_history.append(("You", user_input))
        st.session_state.chat_history.append(("Assistant", PLACEHOLDER))
        st.rerun()

    # Process pending placeholder
    if (
        st.session_state.chat_history
        and st.session_state.chat_history[-1] == ("Assistant", PLACEHOLDER)
    ):
        user_message = (
            st.session_state.chat_history[-2][1]
            if len(st.session_state.chat_history) >= 2
            else ""
        )
        with st.spinner("CURE is analyzing..."):
            try:
                state = CodebaseAnalysisSessionState(user_input=user_message)
                state = orchestrator.run_multiturn_chain(state)
                answer = _extract_content(state.formatted_response)
            except Exception as e:
                logger.error("Chat orchestration error: %s", e, exc_info=True)
                st.error(f"Error: {e}")
                answer = "Sorry, I couldn't process that request. Please try rephrasing."

        st.session_state.chat_history[-1] = ("Assistant", answer)
        st.rerun()

    # Download full history
    if st.session_state.get("chat_history"):
        st.download_button(
            "Download Chat History",
            "\n".join(
                f"{speaker}: {_extract_content(text) if speaker != 'You' else text}"
                for speaker, text in st.session_state["chat_history"]
            ),
            file_name="cure_chat_history.txt",
        )


def page_about():
    """About page with project overview and connection info."""
    col1, col2 = st.columns([1, 3])
    with col1:
        if os.path.isfile(LOGO_SIDEBAR):
            st.image(LOGO_SIDEBAR, use_container_width=True)
    with col2:
        st.markdown(
            "## About CURE\n\n"
            "**CURE** (Codebase Update & Refactor Engine) is a multi-stage C/C++ codebase "
            "health analysis pipeline. It combines fast regex-based static analyzers with "
            "deep static analysis adapters (Lizard, Flawfinder, CCLS/libclang) and "
            "LLM-powered code review to produce actionable health metrics.\n\n"
            "**Key features:**\n\n"
            "- 9 built-in health analyzers (complexity, security, memory, deadlocks, etc.)\n"
            "- Deep static adapters: AST complexity, dead code detection, call graph analysis\n"
            "- Multi-provider LLM support (Anthropic, QGenie, Vertex AI, Azure OpenAI)\n"
            "- Human-in-the-loop agentic code repair\n"
            "- Vector DB ingestion for RAG-powered chat\n"
        )

    st.divider()
    net_ip = st_tools.get_local_ip()
    st.markdown(
        f"**Dashboard access:**  \n"
        f"This machine: [http://localhost:8502](http://localhost:8502)  \n"
        f"Network: [http://{net_ip}:8502](http://{net_ip}:8502)  \n\n"
        f"**Contact:** sendpavanr@gmail.com  \n"
        f"**Model:** `{STREAMLIT_MODEL}`"
    )


def page_faq():
    """FAQ page."""
    st.markdown("## Frequently Asked Questions")
    FAQS = [
        (
            "What can I ask in the chat?",
            "Ask about code health metrics, module dependencies, security findings, "
            "documentation gaps, test coverage, complexity hotspots, dead code, "
            "and refactoring recommendations.",
        ),
        (
            "What data does this use?",
            "It uses precomputed codebase analysis reports (healthreport.json), "
            "dependency graphs, static analysis adapter results, and vector DB "
            "embeddings generated by the CURE pipeline.",
        ),
        (
            "How do I populate the data?",
            "Run the analysis pipeline first:\n\n"
            "```bash\n"
            "python main.py --codebase-path /path/to/project --enable-vector-db --enable-adapters\n"
            "```",
        ),
        (
            "What are deep static adapters?",
            "Adapters powered by real analysis tools instead of regex. "
            "Use `--enable-adapters` to activate Lizard (complexity), "
            "Flawfinder (security), and CCLS/libclang (dead code, call graphs, function metrics).",
        ),
    ]
    query = st.text_input("Search FAQs...")
    for q, a in FAQS:
        if not query or query.lower() in q.lower() or query.lower() in a.lower():
            with st.expander(q):
                st.markdown(a)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    st_tools.app_css()

    # Header logo
    if os.path.isfile(LOGO_MAIN):
        st.image(LOGO_MAIN, use_container_width=False, width=480)

    # Sidebar navigation
    page = st_tools.sidebar(LOGO_SIDEBAR)

    if page == "Chat":
        page_chat()
    elif page == "About":
        page_about()
    elif page == "FAQ":
        page_faq()


if __name__ == "__main__":
    main()
