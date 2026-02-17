"""
streamlit_tools.py

CURE — Codebase Update & Refactor Engine
Shared UI helpers: sidebar, CSS, feedback widgets, chat utilities.

Author: Pavan R
"""

import os
import socket
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

# ── Brand constants ──────────────────────────────────────────────────────────
CURE_CYAN = "#00BCD4"
CURE_DARK_BG = "#0D1B2A"
CURE_CARD_BG = "#1B2838"
CURE_TEXT = "#E0E0E0"
CURE_ACCENT = "#80DEEA"
CURE_GREEN = "#4CAF50"
CURE_GOLD = "#FFD700"


# ═══════════════════════════════════════════════════════════════════════════════
#  Global CSS
# ═══════════════════════════════════════════════════════════════════════════════

def app_css() -> None:
    """Injects CURE-branded global CSS styling."""
    st.markdown(
        f"""
        <style>
        /* ── Base typography ─────────────────────────────────────── */
        html, body, table, th, td {{
            font-family: "SF Pro Display", "Helvetica Neue", Arial, sans-serif !important;
            font-feature-settings: "liga" on, "kern" on;
        }}

        /* ── Table styling ───────────────────────────────────────── */
        table, th, td {{
            background-color: {CURE_DARK_BG} !important;
            color: {CURE_TEXT} !important;
            border-color: #2A2A2A !important;
        }}
        thead th {{
            background-color: {CURE_CARD_BG} !important;
            color: {CURE_CYAN} !important;
            font-weight: 600 !important;
        }}
        table, .stDataFrame table, .stChatMessage table {{
            border-radius: 12px !important;
            overflow: hidden !important;
            margin-bottom: 1em;
        }}

        /* ── Headings ────────────────────────────────────────────── */
        h1, h2, h3 {{
            color: {CURE_ACCENT} !important;
            letter-spacing: 0.3px;
            font-weight: 700;
            border: none;
        }}
        h4, h5, h6 {{
            color: {CURE_TEXT} !important;
            font-weight: 600;
        }}

        hr {{
            border-top: 2px solid #2A2A2A !important;
            margin-top: 16px;
            margin-bottom: 16px;
        }}

        /* ── Sidebar branding ────────────────────────────────────── */
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, {CURE_DARK_BG} 0%, {CURE_CARD_BG} 100%);
        }}
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {{
            color: {CURE_CYAN} !important;
        }}

        /* ── Feedback row ────────────────────────────────────────── */
        .feedback-row {{
            display: flex;
            align-items: center;
            gap: 18px;
            margin-top: 8px;
            margin-bottom: 10px;
        }}
        .feedback-label {{
            font-weight: bold;
            font-size: 16px;
        }}
        .feedback-btn {{
            font-size: 16px;
            padding: 6px 15px;
            border-radius: 5px;
            border: none;
            cursor: pointer;
        }}
        .feedback-summary {{
            background-color: {CURE_CARD_BG};
            border-radius: 6px;
            border: 1px solid {CURE_CYAN};
            padding: 7px 12px;
            color: {CURE_ACCENT};
            font-size: 15px;
            margin-left: 10px;
            display: inline-block;
        }}

        /* ── Chat message tweaks ─────────────────────────────────── */
        .stChatMessage {{
            border-radius: 12px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Response extraction
# ═══════════════════════════════════════════════════════════════════════════════

def extract_answer(agent_response: Any) -> str:
    """
    Extracts the assistant's answer from various response shapes.

    Supports plain strings, dicts with 'content', objects with .content,
    and lists of role-tagged messages.
    """
    try:
        if isinstance(agent_response, str):
            return agent_response

        if isinstance(agent_response, dict) and "content" in agent_response:
            return str(agent_response["content"])

        if hasattr(agent_response, "content"):
            return str(agent_response.content)

        if isinstance(agent_response, list) and agent_response:
            assistant_msgs = [
                msg for msg in agent_response
                if (isinstance(msg, dict) and msg.get("role") == "assistant")
                or (hasattr(msg, "role") and getattr(msg, "role") == "assistant")
            ]
            if assistant_msgs:
                last = assistant_msgs[-1]
                if isinstance(last, dict):
                    return str(last.get("content", "No response."))
                if hasattr(last, "content"):
                    return str(last.content)
                return str(last)

            last = agent_response[-1]
            if isinstance(last, dict):
                return str(last.get("content", last))
            if hasattr(last, "content"):
                return str(last.content)
            return str(last)

        if agent_response is None:
            return "No response."

        return f"Unknown response type: {type(agent_response)}"
    except Exception as e:
        return f"Error extracting answer: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Network helpers
# ═══════════════════════════════════════════════════════════════════════════════

def get_local_ip() -> str:
    """Returns best-effort local IP for dashboard access instructions."""
    ip = "localhost"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass
    return ip


# ═══════════════════════════════════════════════════════════════════════════════
#  Sidebar / Navigation
# ═══════════════════════════════════════════════════════════════════════════════

def sidebar(logo_path: str) -> str:
    """
    Renders the CURE-branded sidebar with logo, navigation, and version info.

    Returns the currently selected page name.
    """
    with st.sidebar:
        # Logo
        if logo_path and os.path.isfile(logo_path):
            try:
                st.image(logo_path, use_container_width=True)
            except Exception:
                pass

        st.markdown(
            f"<h5 style='text-align:center; color:{CURE_CYAN}; margin-top:-10px; "
            f"margin-bottom:18px;'>CURE</h5>",
            unsafe_allow_html=True,
        )

        # Navigation
        st.markdown("### Navigation")
        page = st.radio(
            "Main Menu",
            ("Chat", "About", "FAQ"),
            index=0,
            label_visibility="collapsed",
        )

        st.markdown(
            "<hr style='margin-top: 4px; margin-bottom: 10px;'>",
            unsafe_allow_html=True,
        )

        # Feedback toggle
        feedback_on = feedback_toggle_sidebar()
        st.session_state["feedback_mode"] = feedback_on

        # Version badge
        st.markdown(
            f"<div style='text-align:center; margin-top:20px; padding:8px; "
            f"background:{CURE_CARD_BG}; border-radius:8px; border:1px solid #2A2A2A;'>"
            f"<span style='color:{CURE_ACCENT}; font-size:12px;'>CURE v2.0</span><br>"
            f"<span style='color:#888; font-size:11px;'>Codebase Analysis<br>&amp; Refactor Engine</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    return page


# ═══════════════════════════════════════════════════════════════════════════════
#  Chat context helpers
# ═══════════════════════════════════════════════════════════════════════════════

def get_limited_chat_context(
    history: Sequence[Tuple[str, str]],
    summary: str,
    max_turns: int = 25,
) -> List[Dict[str, str]]:
    """
    Builds a message list for LLM chat APIs from conversation history.

    Puts summarized history into a system message, then appends
    up to max_turns recent user/assistant messages.
    """
    context: List[Dict[str, str]] = []
    if summary:
        context.append({
            "role": "system",
            "content": f"Summary of earlier conversation: {summary}",
        })

    for speaker, text in list(history)[-max_turns:]:
        role = "user" if speaker == "You" else "assistant"
        context.append({"role": role, "content": text})

    return context


@st.cache_resource
def process_uploaded_file(uploaded_file: Any) -> Optional[Any]:
    """
    Placeholder for file processing / indexing logic.

    Implement to parse uploaded files and index their contents
    for downstream retrieval or QA.
    """
    # TODO: Implement actual file processing when needed.
    return None


def summarize_chat(
    messages: Sequence[Tuple[str, str]],
    prev_summary: str = "",
) -> str:
    """
    Summarizes a list of (speaker, text) chat tuples.

    Simple fallback implementation; replace with LLM-based summarizer
    for higher quality.
    """
    chat_text = "\n".join(f"{speaker}: {text}" for speaker, text in messages)
    if not chat_text:
        return prev_summary

    full = (prev_summary + "\n" + chat_text).strip()
    return full[:1000]


# ═══════════════════════════════════════════════════════════════════════════════
#  Feedback helpers
# ═══════════════════════════════════════════════════════════════════════════════

def feedback_toggle_sidebar() -> bool:
    """Displays a sidebar toggle for user feedback participation."""
    help_text = (
        "If enabled, feedback options appear after each response. "
        "All feedback is voluntary and may be used to improve this tool."
    )
    if hasattr(st.sidebar, "toggle"):
        return st.sidebar.toggle("Feedback Mode", help=help_text)
    return st.sidebar.checkbox("Feedback Mode", help=help_text)


def feedback_info_if_enabled() -> None:
    """Shows an info block if feedback mode is on, or a caption if off."""
    if st.session_state.get("feedback_mode", False):
        st.info(
            "**Feedback is optional.**\n\n"
            "A *hallucination* is when the assistant says something factually wrong, "
            "makes up data, or invents results.\n\n"
            "_Your input/feedback may be stored and used to improve CURE._"
        )
    else:
        st.caption("Feedback mode is OFF. No response ratings will be recorded.")


def feedback_widget(
    response_id: int,
    user_message: str,
    bot_response: str,
) -> Optional[Dict[str, Any]]:
    """
    Renders feedback controls (like / dislike / hallucination) for a response.

    Returns a feedback dict if the user interacted, otherwise None.
    """
    if not st.session_state.get("feedback_mode", False):
        return None

    col1, col2, col3, col4, col5 = st.columns([4, 3, 4, 4, 10])

    with col1:
        st.markdown(
            "<span style='font-weight:bold; font-size:16px;'>Rate response:</span>",
            unsafe_allow_html=True,
        )
    with col2:
        liked = st.button("👍 Prefer", key=f"like_{response_id}")
    with col3:
        disliked = st.button("👎 Don't prefer", key=f"dislike_{response_id}")
    with col4:
        halluc = st.checkbox("🤔 Hallucination", key=f"halluc_{response_id}")
    with col5:
        selection: List[str] = []
        if liked:
            selection.append("Prefer")
        if disliked:
            selection.append("Don't prefer")
        if halluc:
            selection.append("Hallucination")

        if selection:
            st.markdown(
                f"<span class='feedback-summary'>"
                f"<b>Selected:</b> {' | '.join(selection)}</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<span style='color:#888; font-size:14px;'>Selected: None</span>",
                unsafe_allow_html=True,
            )

    if liked or disliked or halluc:
        st.success("Thank you for your feedback!")
        return {
            "user_message": user_message,
            "bot_response": bot_response,
            "liked": liked,
            "disliked": disliked,
            "hallucination": halluc,
            "timestamp": pd.Timestamp.now().isoformat(),
        }

    return None
