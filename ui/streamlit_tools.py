"""
streamlit_tools.py

CURE — Codebase Update & Refactor Engine
Shared UI helpers: sidebar, CSS, feedback widgets, chat utilities.

Author: Pavan R
"""

import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
import streamlit as st

# ── Brand constants (Apple-inspired light theme) ────────────────────────────
CURE_PRIMARY = "#007AFF"        # Apple blue
CURE_BG = "#FFFFFF"             # Pure white background
CURE_SURFACE = "#F5F5F7"       # Apple light gray (cards, sections)
CURE_CARD_BG = "#FFFFFF"        # White cards
CURE_TEXT = "#1D1D1F"           # Apple near-black text
CURE_TEXT_SECONDARY = "#86868B" # Apple secondary gray
CURE_ACCENT = "#007AFF"        # Apple blue accent
CURE_GREEN = "#34C759"         # Apple green
CURE_GOLD = "#FF9F0A"          # Apple orange/gold
CURE_RED = "#FF3B30"           # Apple red
CURE_BORDER = "#D2D2D7"        # Apple border gray
CURE_SIDEBAR_BG = "#F5F5F7"    # Light sidebar

# Legacy alias for backwards compatibility
CURE_CYAN = CURE_PRIMARY
CURE_DARK_BG = CURE_SURFACE


# ═══════════════════════════════════════════════════════════════════════════════
#  Global CSS
# ═══════════════════════════════════════════════════════════════════════════════

def app_css() -> None:
    """Injects Apple-inspired light-theme global CSS styling."""
    st.markdown(
        f"""
        <style>
        /* ── Base typography (SF Pro / system font stack) ─────────── */
        html, body, table, th, td {{
            font-family: -apple-system, "SF Pro Display", "SF Pro Text",
                         "Helvetica Neue", Arial, sans-serif !important;
            font-feature-settings: "liga" on, "kern" on;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        /* ── Main content area — clean white bg ──────────────────── */
        .stApp {{
            background-color: {CURE_BG} !important;
        }}
        .main .block-container {{
            background-color: {CURE_BG} !important;
        }}

        /* ── Table styling ───────────────────────────────────────── */
        table, th, td {{
            background-color: {CURE_BG} !important;
            color: {CURE_TEXT} !important;
            border-color: {CURE_BORDER} !important;
        }}
        thead th {{
            background-color: {CURE_SURFACE} !important;
            color: {CURE_TEXT} !important;
            font-weight: 600 !important;
            font-size: 13px !important;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }}
        table, .stDataFrame table, .stChatMessage table {{
            border-radius: 10px !important;
            overflow: hidden !important;
            margin-bottom: 1em;
            border: 1px solid {CURE_BORDER} !important;
        }}

        /* ── Headings ────────────────────────────────────────────── */
        h1, h2, h3 {{
            color: {CURE_TEXT} !important;
            letter-spacing: -0.3px;
            font-weight: 700;
            border: none;
        }}
        h4, h5, h6 {{
            color: {CURE_TEXT} !important;
            font-weight: 600;
        }}

        hr {{
            border-top: 1px solid {CURE_BORDER} !important;
            margin-top: 16px;
            margin-bottom: 16px;
        }}

        /* ── Sidebar — Apple-style light panel ───────────────────── */
        [data-testid="stSidebar"] {{
            background: {CURE_SIDEBAR_BG} !important;
            border-right: 1px solid {CURE_BORDER};
        }}
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {{
            color: {CURE_TEXT} !important;
        }}
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] .stRadio label,
        [data-testid="stSidebar"] .stRadio div[role="radiogroup"] label,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        [data-testid="stSidebar"] .stCheckbox label,
        [data-testid="stSidebar"] .stToggle label,
        [data-testid="stSidebar"] div[data-baseweb="radio"] label {{
            color: {CURE_TEXT} !important;
        }}
        [data-testid="stSidebar"] div[data-baseweb="radio"] div {{
            border-color: {CURE_PRIMARY} !important;
        }}
        [data-testid="stSidebar"] .stToggle span,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] {{
            color: {CURE_TEXT} !important;
        }}

        /* ── Buttons — Apple style ───────────────────────────────── */
        .stButton > button[kind="primary"] {{
            background-color: {CURE_PRIMARY} !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
        }}
        .stButton > button {{
            border-radius: 8px !important;
            border: 1px solid {CURE_BORDER} !important;
            font-weight: 500 !important;
        }}

        /* ── Tabs styling ────────────────────────────────────────── */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            background: {CURE_SURFACE};
            border-radius: 10px;
            padding: 4px;
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 8px !important;
            padding: 8px 16px !important;
            font-weight: 500 !important;
            color: {CURE_TEXT_SECONDARY} !important;
        }}
        .stTabs [aria-selected="true"] {{
            background-color: {CURE_BG} !important;
            color: {CURE_TEXT} !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
        }}

        /* ── Input fields ────────────────────────────────────────── */
        .stTextInput input, .stNumberInput input, .stSelectbox select {{
            border-radius: 8px !important;
            border: 1px solid {CURE_BORDER} !important;
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
            color: {CURE_TEXT};
        }}
        .feedback-btn {{
            font-size: 16px;
            padding: 6px 15px;
            border-radius: 8px;
            border: 1px solid {CURE_BORDER};
            cursor: pointer;
            background: {CURE_BG};
            color: {CURE_TEXT};
        }}
        .feedback-summary {{
            background-color: {CURE_SURFACE};
            border-radius: 8px;
            border: 1px solid {CURE_BORDER};
            padding: 7px 12px;
            color: {CURE_PRIMARY};
            font-size: 15px;
            margin-left: 10px;
            display: inline-block;
        }}

        /* ── Chat message tweaks ─────────────────────────────────── */
        .stChatMessage {{
            border-radius: 12px !important;
            border: 1px solid {CURE_BORDER} !important;
        }}

        /* ── Expander styling ────────────────────────────────────── */
        .streamlit-expanderHeader {{
            font-weight: 600 !important;
            color: {CURE_TEXT} !important;
        }}

        /* ── Metric cards ────────────────────────────────────────── */
        [data-testid="stMetric"] {{
            background: {CURE_SURFACE};
            border-radius: 10px;
            padding: 12px 16px;
            border: 1px solid {CURE_BORDER};
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
    Renders the CURE-branded sidebar with logo, navigation (Chat / About),
    feedback toggle, and version info.

    The four workflow pages (Analyze, Pipeline, Review, Fix & QA) are shown
    as top-level tabs instead of sidebar radio buttons.

    Returns the currently selected sidebar page name, or empty string
    if no sidebar page is active (i.e. the user is on a tab page).
    """
    with st.sidebar:
        # Logo
        if logo_path and os.path.isfile(logo_path):
            try:
                st.image(logo_path, width=180)
            except Exception:
                pass

        st.markdown(
            f"<h5 style='text-align:center; color:{CURE_PRIMARY}; margin-top:-10px; "
            f"margin-bottom:18px; font-weight:700;'>CURE</h5>",
            unsafe_allow_html=True,
        )

        st.markdown(
            "<hr style='margin-top: 4px; margin-bottom: 10px;'>",
            unsafe_allow_html=True,
        )

        # Sidebar navigation — only Chat and About
        st.markdown("### Navigation")
        page = st.radio(
            "Sidebar Menu",
            ("Workflow", "Chat", "About"),
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

        # ── Configuration toggles ────────────────────────────────────
        st.markdown(
            "<hr style='margin-top: 4px; margin-bottom: 10px;'>",
            unsafe_allow_html=True,
        )
        st.markdown("### Configuration")

        # HITL toggle
        if "enable_hitl" not in st.session_state:
            st.session_state["enable_hitl"] = False
        hitl_on = st.toggle(
            "Enable HITL",
            value=st.session_state.get("enable_hitl", False),
            help="Enable Human-in-the-Loop feedback pipeline (requires PostgreSQL)",
        )
        st.session_state["enable_hitl"] = hitl_on

        # Telemetry toggle
        if "enable_telemetry" not in st.session_state:
            st.session_state["enable_telemetry"] = True
        telemetry_on = st.toggle(
            "Telemetry",
            value=st.session_state.get("enable_telemetry", True),
            help="Silent usage tracking — issues found/fixed, LLM usage, run durations",
        )
        st.session_state["enable_telemetry"] = telemetry_on

        st.markdown(
            "<hr style='margin-top: 4px; margin-bottom: 10px;'>",
            unsafe_allow_html=True,
        )

        # Version badge
        st.markdown(
            f"<div style='text-align:center; margin-top:20px; padding:10px; "
            f"background:{CURE_BG}; border-radius:10px; border:1px solid {CURE_BORDER};'>"
            f"<span style='color:{CURE_PRIMARY}; font-size:12px; font-weight:600;'>CURE v2.0</span><br>"
            f"<span style='color:{CURE_TEXT_SECONDARY}; font-size:11px;'>Codebase Analysis<br>&amp; Refactor Engine</span>"
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline phase tracker
# ═══════════════════════════════════════════════════════════════════════════════

PHASE_ICONS = {
    "completed": "✅",
    "in_progress": "⏳",
    "pending": "○",
    "error": "❌",
}


def render_phase_tracker(
    phase_names: Dict[int, str],
    phase_statuses: Dict[int, str],
) -> None:
    """
    Renders a pipeline phase progress tracker.

    Args:
        phase_names: Mapping of phase number to display name.
        phase_statuses: Mapping of phase number to status string
                       ("pending", "in_progress", "completed", "error").
    """
    cols = st.columns(len(phase_names))
    for i, (phase_num, name) in enumerate(sorted(phase_names.items())):
        status = phase_statuses.get(phase_num, "pending")
        icon = PHASE_ICONS.get(status, "○")

        color = CURE_TEXT_SECONDARY
        bg = CURE_SURFACE
        if status == "completed":
            color = CURE_GREEN
            bg = "#F0FDF4"
        elif status == "in_progress":
            color = CURE_PRIMARY
            bg = "#EFF6FF"
        elif status == "error":
            color = CURE_RED
            bg = "#FEF2F2"

        with cols[i]:
            st.markdown(
                f"<div style='text-align:center; padding:10px; background:{bg}; "
                f"border-radius:10px; border:1px solid {CURE_BORDER};'>"
                f"<span style='font-size:24px;'>{icon}</span><br>"
                f"<span style='color:{color}; font-size:12px; font-weight:600;'>"
                f"Phase {phase_num}</span><br>"
                f"<span style='color:{CURE_TEXT}; font-size:11px;'>{name}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  Log stream renderer
# ═══════════════════════════════════════════════════════════════════════════════

LOG_LEVEL_COLORS = {
    "INFO": "#1D1D1F",
    "WARNING": "#FF9F0A",
    "ERROR": "#FF3B30",
    "DEBUG": "#86868B",
}


def render_log_stream(
    logs: list,
    max_lines: int = 100,
) -> None:
    """
    Renders a scrollable log stream with color-coded severity.

    Args:
        logs: List of log entry dicts with keys: timestamp, level, message.
        max_lines: Maximum number of lines to display.
    """
    recent = logs[-max_lines:] if len(logs) > max_lines else logs

    if not recent:
        st.caption("Waiting for log output...")
        return

    lines_html = []
    for entry in recent:
        if isinstance(entry, dict):
            ts = entry.get("timestamp", "")
            level = entry.get("level", "INFO")
            msg = entry.get("message", "")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
            ts, level, msg = str(entry[0]), str(entry[1]), str(entry[2])
        else:
            msg = str(entry)
            ts, level = "", "INFO"

        # Skip internal sentinel messages
        if msg == "__DONE__":
            continue

        color = LOG_LEVEL_COLORS.get(level, CURE_TEXT)
        # Escape HTML in message
        safe_msg = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines_html.append(
            f"<span style='color:#666;'>{ts}</span> "
            f"<span style='color:{color};'>{safe_msg}</span>"
        )

    html = (
        f"<div style='background:{CURE_SURFACE}; border:1px solid {CURE_BORDER}; "
        f"border-radius:10px; padding:14px; max-height:400px; overflow-y:auto; "
        f"font-family:\"SF Mono\",\"Menlo\",\"Fira Code\",monospace; font-size:12px; "
        f"line-height:1.7;'>"
        + "<br>".join(lines_html)
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  Severity badge
# ═══════════════════════════════════════════════════════════════════════════════

SEVERITY_COLORS = {
    "Critical": "#FF3B30",
    "High": "#FF6B35",
    "Medium": "#FF9F0A",
    "Low": "#34C759",
}


def severity_badge(severity: str) -> str:
    """Returns an HTML badge for a severity level."""
    color = SEVERITY_COLORS.get(severity, CURE_TEXT_SECONDARY)
    return (
        f"<span style='background:{color}15; color:{color}; "
        f"padding:3px 10px; border-radius:6px; font-size:12px; "
        f"font-weight:600;'>{severity}</span>"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Folder / file browser widget
# ═══════════════════════════════════════════════════════════════════════════════

def _get_system_roots() -> List[str]:
    """Return sensible filesystem roots for the current OS."""
    system = platform.system()
    if system == "Windows":
        # List available drive letters
        import string
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                drives.append(drive)
        return drives if drives else ["C:\\"]
    elif system == "Darwin":
        roots = [str(Path.home()), "/", "/Volumes"]
        return [r for r in roots if os.path.isdir(r)]
    else:
        roots = [str(Path.home()), "/"]
        return [r for r in roots if os.path.isdir(r)]


def _list_directory(path: str) -> Tuple[List[str], List[str]]:
    """
    List directories and files under *path*.
    Returns (sorted_dirs, sorted_files).  Silently skips permission errors.
    """
    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    name = entry.name
                    if name.startswith("."):
                        continue  # hide dotfiles by default
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(name)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(name)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    dirs.sort(key=str.lower)
    files.sort(key=str.lower)
    return dirs, files


def _open_native_folder_dialog(initial_dir: str = "", title: str = "Select Folder") -> str:
    """
    Open the native OS folder picker (Finder / Explorer / GTK dialog).

    Runs tkinter in a subprocess to avoid threading conflicts with Streamlit.
    Returns the selected path, or empty string if cancelled.
    """
    if not initial_dir or not os.path.isdir(initial_dir):
        initial_dir = str(Path.home())

    script = (
        "import tkinter as tk; "
        "from tkinter import filedialog; "
        "root = tk.Tk(); "
        "root.withdraw(); "
        "root.attributes('-topmost', True); "
        f"path = filedialog.askdirectory(initialdir={initial_dir!r}, title={title!r}); "
        "print(path if path else '')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _open_native_file_dialog(
    initial_dir: str = "",
    title: str = "Select File",
    filetypes: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Open the native OS file picker (Finder / Explorer / GTK dialog).

    Runs tkinter in a subprocess to avoid threading conflicts with Streamlit.
    Returns the selected file path, or empty string if cancelled.
    """
    if not initial_dir or not os.path.isdir(initial_dir):
        initial_dir = str(Path.home())

    if filetypes:
        ft_str = repr(filetypes)
    else:
        ft_str = repr([("All files", "*.*")])

    script = (
        "import tkinter as tk; "
        "from tkinter import filedialog; "
        "root = tk.Tk(); "
        "root.withdraw(); "
        "root.attributes('-topmost', True); "
        f"path = filedialog.askopenfilename(initialdir={initial_dir!r}, title={title!r}, filetypes={ft_str}); "
        "print(path if path else '')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def folder_browser(
    label: str = "Browse",
    default_path: str = "",
    key: str = "folder_browser",
    show_files: bool = True,
    file_extensions: Optional[List[str]] = None,
    help_text: str = "",
) -> str:
    """
    Renders a folder/file path input with a Browse button that opens the
    **native OS file dialog** (Finder on macOS, Explorer on Windows,
    GTK dialog on Linux).

    Falls back to the in-page browser if the native dialog is unavailable
    (e.g. headless server, missing tkinter).

    Args:
        label: Display label for the text input.
        default_path: Initial value shown in the text input.
        key: Unique Streamlit widget key prefix.
        show_files: If True and file_extensions is set, opens a native
                    file picker instead of a folder picker.
        file_extensions: If set, opens a file picker filtered to these
                        extensions (e.g. [".c", ".cpp", ".h"]).
        help_text: Tooltip / help string for the text input.

    Returns:
        The selected (or typed) path string.
    """

    # ── Session state keys ────────────────────────────────────────────────
    result_key = f"{key}_native_result"
    browse_key = f"{key}_browsing"
    nav_key = f"{key}_nav_path"

    if browse_key not in st.session_state:
        st.session_state[browse_key] = False
    if nav_key not in st.session_state:
        st.session_state[nav_key] = ""

    # Use the native result if one was just picked
    effective_default = default_path
    if result_key in st.session_state and st.session_state[result_key]:
        effective_default = st.session_state[result_key]

    # ── Text input + Browse button side by side ───────────────────────────
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        typed_path = st.text_input(
            label,
            value=effective_default,
            key=f"{key}_text",
            help=help_text or "Type a path or click Browse to navigate.",
        )
    with col_btn:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("📂 Browse", key=f"{key}_btn"):
            start = typed_path.strip() if typed_path.strip() else str(Path.home())
            if not os.path.isdir(start):
                start = os.path.dirname(start) if os.path.exists(os.path.dirname(start)) else str(Path.home())

            # Decide: file picker vs folder picker
            picked = ""
            if show_files and file_extensions:
                # Build filetypes for native dialog
                ext_patterns = " ".join(f"*{e}" for e in file_extensions)
                filetypes = [("Matching files", ext_patterns), ("All files", "*.*")]
                picked = _open_native_file_dialog(
                    initial_dir=start,
                    title=f"Select File — {label}",
                    filetypes=filetypes,
                )
            else:
                picked = _open_native_folder_dialog(
                    initial_dir=start,
                    title=f"Select Folder — {label}",
                )

            if picked:
                # Native dialog succeeded
                st.session_state[result_key] = picked
                st.session_state[browse_key] = False
                st.rerun()
            else:
                # Fallback: open in-page browser (dialog was cancelled or unavailable)
                st.session_state[nav_key] = start
                st.session_state[browse_key] = True
                st.rerun()

    # ── In-page fallback browser panel ────────────────────────────────────
    if st.session_state[browse_key]:
        current = st.session_state[nav_key]

        # Validate current path
        if not current or not os.path.isdir(current):
            current = str(Path.home())
            st.session_state[nav_key] = current

        with st.container():
            st.markdown(
                f"<div style='background:{CURE_SURFACE}; border:1px solid {CURE_BORDER}; "
                f"border-radius:10px; padding:12px; margin-bottom:12px;'>"
                f"<span style='color:{CURE_TEXT}; font-weight:600;'>📂 Folder Browser</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Current path display
            st.code(current, language=None)

            # Action row: quick roots, parent, select, close
            root_cols = st.columns([2, 2, 2, 2, 2])

            with root_cols[0]:
                roots = _get_system_roots()
                root_choice = st.selectbox(
                    "Quick Jump",
                    [""] + roots,
                    key=f"{key}_roots",
                    label_visibility="collapsed",
                    format_func=lambda x: "📌 Quick Jump…" if x == "" else x,
                )
                if root_choice:
                    st.session_state[nav_key] = root_choice
                    st.rerun()

            with root_cols[1]:
                parent = str(Path(current).parent)
                if st.button("⬆ Parent", key=f"{key}_parent", disabled=(parent == current)):
                    st.session_state[nav_key] = parent
                    st.rerun()

            with root_cols[2]:
                if st.button("🏠 Home", key=f"{key}_home"):
                    st.session_state[nav_key] = str(Path.home())
                    st.rerun()

            with root_cols[3]:
                if st.button("✅ Select", key=f"{key}_select", type="primary"):
                    st.session_state[result_key] = current
                    st.session_state[browse_key] = False
                    st.rerun()

            with root_cols[4]:
                if st.button("✖ Close", key=f"{key}_close"):
                    st.session_state[browse_key] = False
                    st.rerun()

            st.markdown(
                f"<hr style='margin:6px 0; border-color:{CURE_BORDER};'>",
                unsafe_allow_html=True,
            )

            # ── Directory listing ─────────────────────────────────────────
            dirs, files = _list_directory(current)

            if not dirs and not files:
                st.caption("Empty directory or no permissions.")
            else:
                # Show directories first
                for d in dirs:
                    full = os.path.join(current, d)
                    if st.button(f"📁 {d}", key=f"{key}_d_{d}"):
                        st.session_state[nav_key] = full
                        st.rerun()

                # Optionally show files
                if show_files and files:
                    filtered = files
                    if file_extensions:
                        ext_set = {e.lower().lstrip(".") for e in file_extensions}
                        filtered = [
                            f for f in files
                            if f.rsplit(".", 1)[-1].lower() in ext_set
                        ]
                    if filtered:
                        st.markdown(
                            f"<span style='color:{CURE_TEXT_SECONDARY}; font-size:12px; "
                            f"font-weight:600;'>Files ({len(filtered)})</span>",
                            unsafe_allow_html=True,
                        )
                        for f in filtered[:100]:  # cap at 100 to avoid UI lag
                            st.caption(f"  📄 {f}")
                        if len(filtered) > 100:
                            st.caption(f"  … and {len(filtered) - 100} more files")

        # Return the navigated path when browsing
        return current

    # Clear the native result after it's been consumed by the text input
    if result_key in st.session_state and st.session_state[result_key]:
        picked_val = st.session_state[result_key]
        st.session_state[result_key] = ""
        return picked_val

    return typed_path
