"""
app.py

CURE — Codebase Update & Refactor Engine
Streamlit dashboard for codebase ingestion, real-time analysis pipeline,
human-in-the-loop review, agentic code repair, and interactive chat.

Author: Pavan R
"""

import os
import re
import io
import json
import logging
import time
import threading
from pathlib import Path
from queue import Queue, Empty

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

# --- Background workers ---
try:
    from ui.background_workers import (
        run_analysis_background,
        run_fixer_background,
        ANALYSIS_PHASES,
        FIXER_PHASES,
    )
    WORKERS_AVAILABLE = True
except ImportError:
    WORKERS_AVAILABLE = False

# --- Feedback & QA helpers ---
try:
    from ui.feedback_helpers import (
        results_to_dataframe,
        dataframe_to_directives,
        export_to_excel_bytes,
        build_qa_traceability_report,
        compute_summary_stats,
    )
    FEEDBACK_HELPERS_AVAILABLE = True
except ImportError:
    FEEDBACK_HELPERS_AVAILABLE = False

try:
    from ui.qa_inspector import QAInspector, create_zip_archive
    QA_INSPECTOR_AVAILABLE = True
except ImportError:
    QA_INSPECTOR_AVAILABLE = False

# --- Config imports ---
try:
    from utils.parsers.global_config_parser import GlobalConfig
    _gc = GlobalConfig()
    STREAMLIT_MODEL = _gc.get("llm.streamlit_model") or "qgenie::qwen2.5-14b-1m"
except Exception:
    try:
        from utils.parsers.env_parser import EnvConfig
        _ec = EnvConfig()
        STREAMLIT_MODEL = _ec.get("STREAMLIT_MODEL") or "qgenie::qwen2.5-14b-1m"
    except Exception:
        STREAMLIT_MODEL = "qgenie::qwen2.5-14b-1m"

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
    # Chat (existing)
    "chat_history": [],
    "chat_summary": "",
    "all_feedback": [],
    "feedback_mode": False,
    "debug_mode": False,
    "active_page": "Analyze",
    # Ingestion & analysis
    "analysis_mode": "LLM Code Review",
    "codebase_path": "",
    "output_dir": "./out",
    "dependency_granularity": "File",
    "max_files": 2000,
    "batch_size": 25,
    "use_llm": True,
    "enable_adapters": False,
    "exclude_dirs": "",
    # Pipeline state
    "analysis_in_progress": False,
    "analysis_complete": False,
    "analysis_results": [],
    "analysis_metrics": {},
    "pipeline_logs": [],
    "phase_statuses": {},
    "analysis_thread": None,
    "log_queue": None,
    "result_store": {},
    # Review & feedback
    "feedback_df": None,
    # Fixer state
    "fixer_in_progress": False,
    "fixer_complete": False,
    "fixer_logs": [],
    "fixer_phase_statuses": {},
    "fixer_thread": None,
    "fixer_log_queue": None,
    "fixer_result_store": {},
    "directives_path": None,
    # QA
    "qa_results": [],
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


def _validate_codebase_path(path: str) -> tuple:
    """Validate that a path exists and contains C/C++ files."""
    p = Path(path)
    if not p.exists():
        return False, "Path does not exist."
    if not p.is_dir():
        return False, "Path is not a directory."
    cpp_exts = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}
    found = any(p.rglob(f"*{ext}") for ext in cpp_exts)
    if not found:
        return False, "No C/C++ files found in directory."
    return True, "Valid C/C++ codebase."


def _drain_log_queue(queue_obj: Queue, target_list: list) -> bool:
    """Drain all pending messages from a Queue into a list. Returns True if __DONE__ found."""
    done = False
    try:
        while True:
            entry = queue_obj.get_nowait()
            if isinstance(entry, dict) and entry.get("message") == "__DONE__":
                done = True
            else:
                target_list.append(entry)
    except Empty:
        pass
    return done


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: Analyze (Ingestion & Configuration)
# ═══════════════════════════════════════════════════════════════════════════════

def page_analyze():
    """Codebase ingestion controls and analysis configuration."""
    st.markdown(
        "<h2 style='text-align:center; margin-top:-10px;'>"
        "Codebase Analysis</h2>",
        unsafe_allow_html=True,
    )

    if st.session_state.get("analysis_in_progress"):
        st.warning("Analysis is currently running. Check the **Pipeline** page for progress.")
        return

    if st.session_state.get("analysis_complete"):
        st.success("Analysis complete! Review results on the **Review** page.")
        if st.button("Start New Analysis"):
            for key in [
                "analysis_complete", "analysis_results", "analysis_metrics",
                "pipeline_logs", "phase_statuses", "feedback_df",
                "fixer_complete", "fixer_logs", "qa_results",
                "result_store", "directives_path",
            ]:
                st.session_state[key] = _DEFAULTS.get(key, None)
            st.rerun()
        return

    # ── Input mode ────────────────────────────────────────────────────────
    st.markdown("### Input")
    input_mode = st.radio(
        "Input Source",
        ["Local Folder", "Upload Files"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if input_mode == "Local Folder":
        codebase_path = st_tools.folder_browser(
            label="Codebase Path",
            default_path=st.session_state.get("codebase_path", "./codebase"),
            key="analyze_codebase_browser",
            show_files=True,
            file_extensions=[".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"],
            help_text="Absolute or relative path to the C/C++ project directory.",
        )
        st.session_state["codebase_path"] = codebase_path

        if codebase_path:
            valid, msg = _validate_codebase_path(codebase_path)
            if valid:
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")
    else:
        uploaded = st.file_uploader(
            "Upload C/C++ files",
            accept_multiple_files=True,
            type=["c", "cpp", "cc", "cxx", "h", "hpp", "hh", "hxx"],
        )
        if uploaded:
            # Save to temp directory
            upload_dir = os.path.join(st.session_state["output_dir"], "_uploads")
            os.makedirs(upload_dir, exist_ok=True)
            for f in uploaded:
                with open(os.path.join(upload_dir, f.name), "wb") as out:
                    out.write(f.getbuffer())
            st.session_state["codebase_path"] = upload_dir
            st.success(f"✅ {len(uploaded)} files uploaded to staging area.")

    st.divider()

    # ── Analysis configuration ────────────────────────────────────────────
    st.markdown("### Configuration")

    col1, col2 = st.columns(2)
    with col1:
        analysis_mode = st.selectbox(
            "Analysis Mode",
            ["LLM Code Review", "Static Analysis Only"],
            help=(
                "**LLM Code Review**: Per-file semantic analysis using an LLM (produces Excel report).\n\n"
                "**Static Analysis Only**: Fast regex-based 7-phase pipeline (produces health report JSON)."
            ),
        )
        st.session_state["analysis_mode"] = analysis_mode

        dep_granularity = st.selectbox(
            "Dependency Granularity",
            ["File", "Module", "Package"],
            help=(
                "**File**: Individual source/header files.\n\n"
                "**Module**: Group by directory (component-level).\n\n"
                "**Package**: Top-level architecture layers."
            ),
        )
        st.session_state["dependency_granularity"] = dep_granularity

    with col2:
        max_files = st.number_input("Max Files", min_value=1, max_value=50000, value=2000)
        st.session_state["max_files"] = max_files

        batch_size = st.number_input("Batch Size", min_value=1, max_value=200, value=25)
        st.session_state["batch_size"] = batch_size

    # Advanced options
    with st.expander("Advanced Options"):
        col1, col2 = st.columns(2)
        with col1:
            enable_adapters = st.checkbox(
                "Enable Deep Adapters (Lizard, Flawfinder, CCLS)",
                value=False,
            )
            st.session_state["enable_adapters"] = enable_adapters

        with col2:
            exclude_dirs = st.text_input(
                "Exclude Directories (comma-separated)",
                value="",
                help="e.g., test,third_party,build",
            )
            st.session_state["exclude_dirs"] = exclude_dirs

        output_dir = st_tools.folder_browser(
            label="Output Directory",
            default_path=st.session_state.get("output_dir", "./out"),
            key="analyze_output_browser",
            show_files=False,
            help_text="Directory where analysis reports will be saved.",
        )
        st.session_state["output_dir"] = output_dir

    st.divider()

    # ── Start analysis ────────────────────────────────────────────────────
    can_start = bool(st.session_state.get("codebase_path"))
    if can_start:
        valid, _ = _validate_codebase_path(st.session_state["codebase_path"])
        can_start = valid

    if st.button("🚀 Start Analysis", type="primary", disabled=not can_start):
        if not WORKERS_AVAILABLE:
            st.error("Background workers module not available. Check ui/background_workers.py.")
            return

        # Prepare config
        exclude = [
            d.strip()
            for d in st.session_state.get("exclude_dirs", "").split(",")
            if d.strip()
        ]
        config = {
            "codebase_path": st.session_state["codebase_path"],
            "output_dir": st.session_state["output_dir"],
            "analysis_mode": (
                "llm_exclusive"
                if st.session_state["analysis_mode"] == "LLM Code Review"
                else "static"
            ),
            "dependency_granularity": st.session_state["dependency_granularity"],
            "use_llm": st.session_state["analysis_mode"] == "LLM Code Review",
            "enable_adapters": st.session_state.get("enable_adapters", False),
            "max_files": st.session_state.get("max_files", 2000),
            "batch_size": st.session_state.get("batch_size", 25),
            "exclude_dirs": exclude,
        }

        # Initialize queue and result store
        log_queue = Queue()
        result_store = {"status": "running", "phase_statuses": {}}

        st.session_state["log_queue"] = log_queue
        st.session_state["result_store"] = result_store
        st.session_state["pipeline_logs"] = []
        st.session_state["phase_statuses"] = {i: "pending" for i in range(1, 8)}

        # Launch background thread
        t = threading.Thread(
            target=run_analysis_background,
            args=(config, log_queue, result_store),
            daemon=True,
        )
        t.start()
        st.session_state["analysis_thread"] = t
        st.session_state["analysis_in_progress"] = True
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: Pipeline (Real-Time Progress Terminal)
# ═══════════════════════════════════════════════════════════════════════════════

def page_pipeline():
    """Real-time pipeline terminal showing analysis progress."""
    st.markdown(
        "<h2 style='text-align:center; margin-top:-10px;'>"
        "Analysis Pipeline</h2>",
        unsafe_allow_html=True,
    )

    in_progress = st.session_state.get("analysis_in_progress", False)
    is_complete = st.session_state.get("analysis_complete", False)

    if not in_progress and not is_complete:
        st.info("No analysis running. Go to **Analyze** to start one.")
        return

    # Phase tracker
    phases = ANALYSIS_PHASES if WORKERS_AVAILABLE else {i: f"Phase {i}" for i in range(1, 8)}
    phase_statuses = st.session_state.get("phase_statuses", {})

    # Update from result_store (thread-safe read)
    result_store = st.session_state.get("result_store", {})
    if "phase_statuses" in result_store:
        phase_statuses.update(result_store["phase_statuses"])
        st.session_state["phase_statuses"] = phase_statuses

    st_tools.render_phase_tracker(phases, phase_statuses)
    st.divider()

    # Drain log queue
    log_queue = st.session_state.get("log_queue")
    if log_queue:
        done = _drain_log_queue(log_queue, st.session_state["pipeline_logs"])
        if done and in_progress:
            st.session_state["analysis_in_progress"] = False
            st.session_state["analysis_complete"] = True

            # Harvest results from shared store
            if result_store.get("status") == "success":
                st.session_state["analysis_results"] = result_store.get("analysis_results", [])
                st.session_state["analysis_metrics"] = result_store.get("analysis_metrics", {})
            st.rerun()

    # Log stream
    st.markdown("### Console Output")
    st_tools.render_log_stream(st.session_state.get("pipeline_logs", []))

    if in_progress:
        # Auto-refresh while running
        time.sleep(0.5)
        st.rerun()

    if is_complete:
        st.divider()
        st.success("✅ Analysis complete!")

        # Summary metrics
        results = st.session_state.get("analysis_results", [])
        metrics = st.session_state.get("analysis_metrics", {})

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Issues Found", len(results))
        with col2:
            overall = metrics.get("overall_health", {})
            score = overall.get("score", "N/A") if isinstance(overall, dict) else "N/A"
            st.metric("Health Score", f"{score}/100" if score != "N/A" else "N/A")
        with col3:
            stats = metrics.get("statistics", {})
            files = stats.get("processed_files", stats.get("total_files", len(results)))
            st.metric("Files Analyzed", files)

        col1, col2 = st.columns(2)
        with col1:
            report_path = result_store.get("report_path") or result_store.get("health_report_path")
            if report_path and os.path.exists(str(report_path)):
                with open(str(report_path), "rb") as f:
                    st.download_button(
                        "📥 Download Report",
                        data=f.read(),
                        file_name=os.path.basename(str(report_path)),
                    )
        with col2:
            if results:
                st.success("✅ Analysis complete — switch to the **Review** tab to inspect results.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: Review & Feedback (HITL)
# ═══════════════════════════════════════════════════════════════════════════════

def page_review():
    """Interactive review of analysis results with editable feedback columns."""
    st.markdown(
        "<div style='display:flex; align-items:center; gap:12px; margin-bottom:16px;'>"
        "<span style='font-size:36px;'>📋</span>"
        "<div>"
        "<h2 style='margin:0; padding:0;'>Review & Feedback</h2>"
        "<span style='color:#888; font-size:14px;'>Inspect findings, set actions, and export directives</span>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    results = st.session_state.get("analysis_results", [])
    if not results:
        st.info(
            "No analysis results available. Run an analysis from the **Analyze** tab first."
        )
        return

    if not FEEDBACK_HELPERS_AVAILABLE:
        st.error("Feedback helpers module not available.")
        return

    # Build DataFrame if not cached
    if st.session_state.get("feedback_df") is None:
        st.session_state["feedback_df"] = results_to_dataframe(results)

    df = st.session_state["feedback_df"]

    # Normalize severity values to title case for consistent counting
    if "Severity" in df.columns:
        df["Severity"] = df["Severity"].astype(str).str.strip().str.title()

    # ── Filters ───────────────────────────────────────────────────────────
    with st.expander("🔍 Filters", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            sev_options = sorted(df["Severity"].unique().tolist()) if "Severity" in df.columns else []
            sev_filter = st.multiselect("Severity", sev_options, default=sev_options)
        with col2:
            cat_options = sorted(df["Category"].unique().tolist()) if "Category" in df.columns else []
            cat_filter = st.multiselect("Category", cat_options, default=cat_options)
        with col3:
            file_options = sorted(df["File"].unique().tolist()) if "File" in df.columns else []
            file_filter = st.multiselect("File", file_options, default=file_options)

    # Apply filters
    mask = pd.Series([True] * len(df), index=df.index)
    if sev_filter and "Severity" in df.columns:
        mask &= df["Severity"].isin(sev_filter)
    if cat_filter and "Category" in df.columns:
        mask &= df["Category"].isin(cat_filter)
    if file_filter and "File" in df.columns:
        mask &= df["File"].isin(file_filter)

    filtered = df[mask].copy()

    # ── Summary metrics (modern cards) ────────────────────────────────────
    stats = compute_summary_stats(filtered)

    def _metric_card(label, value, color, icon):
        return (
            f"<div style='background:{st_tools.CURE_SURFACE}; border:1px solid {st_tools.CURE_BORDER}; "
            f"border-radius:12px; padding:16px 20px; text-align:center;'>"
            f"<div style='font-size:22px; margin-bottom:4px;'>{icon}</div>"
            f"<div style='font-size:28px; font-weight:700; color:{color};'>{value}</div>"
            f"<div style='font-size:12px; color:{st_tools.CURE_TEXT_SECONDARY}; font-weight:500; "
            f"text-transform:uppercase; letter-spacing:0.5px; margin-top:2px;'>{label}</div>"
            f"</div>"
        )

    mcols = st.columns(7)
    cards = [
        ("Total Issues", stats["total"], st_tools.CURE_TEXT, "📊"),
        ("Critical", stats["critical"], st_tools.CURE_RED, "🔴"),
        ("High", stats["high"], "#FF6B35", "🟠"),
        ("Medium", stats["medium"], st_tools.CURE_GOLD, "🟡"),
        ("Low", stats["low"], st_tools.CURE_GREEN, "🟢"),
        ("To Fix", stats["to_fix"], st_tools.CURE_PRIMARY, "🔧"),
        ("To Skip", stats["to_skip"], st_tools.CURE_TEXT_SECONDARY, "⏭️"),
    ]
    for i, (label, value, color, icon) in enumerate(cards):
        with mcols[i]:
            st.markdown(_metric_card(label, value, color, icon), unsafe_allow_html=True)

    # File coverage
    unique_files = stats.get("unique_files", 0)
    st.markdown(
        f"<div style='text-align:center; margin:12px 0 4px 0; color:{st_tools.CURE_TEXT_SECONDARY}; font-size:13px;'>"
        f"📁 <b>{unique_files}</b> unique file{'s' if unique_files != 1 else ''} affected"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Editable data table ───────────────────────────────────────────────
    st.markdown(
        "<div style='display:flex; align-items:center; gap:8px; margin-bottom:8px;'>"
        "<span style='font-size:20px;'>📝</span>"
        "<span style='font-size:18px; font-weight:600;'>Issue Table</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Edit the **Action** and **Notes** columns to provide feedback. "
        "Choose **Skip** to ignore false positives, **Auto-fix** to apply the suggested fix, "
        "or **Review** to flag for manual inspection with custom notes."
    )

    column_config = {
        "Action": st.column_config.SelectboxColumn(
            "Action",
            options=["Auto-fix", "Skip", "Review"],
            default="Auto-fix",
            width="small",
        ),
        "Notes": st.column_config.TextColumn(
            "Notes",
            help="Add constraints or feedback for the fixer agent.",
            width="medium",
        ),
        "Severity": st.column_config.TextColumn("Severity", width="small"),
        "Confidence": st.column_config.TextColumn("Confidence", width="small"),
        "Line": st.column_config.NumberColumn("Line", width="small"),
    }

    edited = st.data_editor(
        filtered,
        use_container_width=True,
        column_config=column_config,
        disabled=[
            "File", "Title", "Severity", "Confidence", "Category",
            "Line", "Description", "Suggestion", "Code", "Fixed_Code",
        ],
        hide_index=True,
        key="review_editor",
    )

    # Write edits back to the full DataFrame
    if edited is not None:
        for col in ["Action", "Notes"]:
            if col in edited.columns:
                df.loc[edited.index, col] = edited[col]
        st.session_state["feedback_df"] = df

    st.divider()

    # ── Downloads & proceed ───────────────────────────────────────────────
    st.markdown(
        "<div style='display:flex; align-items:center; gap:8px; margin-bottom:8px;'>"
        "<span style='font-size:20px;'>📦</span>"
        "<span style='font-size:18px; font-weight:600;'>Export & Proceed</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)

    with col1:
        excel_bytes = export_to_excel_bytes(df)
        st.download_button(
            "📥 Download Excel",
            data=excel_bytes,
            file_name="cure_analysis_review.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col2:
        # Sanitize non-serializable values before JSON export
        export_df = df.copy()
        for col in export_df.columns:
            export_df[col] = export_df[col].apply(
                lambda v: str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v
            )
        json_str = export_df.to_json(orient="records", indent=2)
        st.download_button(
            "📥 Download JSON",
            data=json_str,
            file_name="cure_analysis_review.json",
            mime="application/json",
        )
    with col3:
        if st.button("⚡ Proceed to Fix & QA", type="primary"):
            out_dir = st.session_state.get("output_dir", "./out")
            directives_path = os.path.join(out_dir, "agent_directives.jsonl")
            dataframe_to_directives(df, directives_path)
            st.session_state["directives_path"] = directives_path
            st.success("✅ Directives saved — switch to the **Fix & QA** tab.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: Fix & QA
# ═══════════════════════════════════════════════════════════════════════════════

def page_fixer_qa():
    """Apply fixes based on feedback and run QA validation."""
    st.markdown(
        "<h2 style='text-align:center; margin-top:-10px;'>"
        "Fix & QA Validation</h2>",
        unsafe_allow_html=True,
    )

    directives_path = st.session_state.get("directives_path")
    fixer_in_progress = st.session_state.get("fixer_in_progress", False)
    fixer_complete = st.session_state.get("fixer_complete", False)

    # ── Section 1: Configuration ──────────────────────────────────────────
    if not fixer_in_progress and not fixer_complete:
        st.markdown("### 1. Fixer Configuration")

        if not directives_path or not os.path.exists(str(directives_path)):
            st.warning(
                "No directives file found. Complete your review on the **Review** page first, "
                "then click **Proceed to Fix & QA**."
            )
            # Allow manual upload
            uploaded = st.file_uploader("Or upload a directives JSONL file", type=["jsonl", "json"])
            if uploaded:
                out_dir = st.session_state.get("output_dir", "./out")
                os.makedirs(out_dir, exist_ok=True)
                directives_path = os.path.join(out_dir, "agent_directives.jsonl")
                with open(directives_path, "wb") as f:
                    f.write(uploaded.getbuffer())
                st.session_state["directives_path"] = directives_path
                st.success(f"Directives uploaded: {directives_path}")
            else:
                return

        st.success(f"Directives: `{directives_path}`")

        output_dir = st_tools.folder_browser(
            label="Output Directory for Fixed Files",
            default_path=st.session_state.get("output_dir", "./out"),
            key="fixer_output_browser",
            show_files=False,
            help_text="Directory where fixed source files will be written.",
        )
        dry_run = st.checkbox("Dry Run (simulate without writing)", value=False)

        st.divider()

        if st.button("🔧 Apply Fixes", type="primary"):
            if not WORKERS_AVAILABLE:
                st.error("Background workers not available.")
                return

            config = {
                "directives_path": directives_path,
                "codebase_path": st.session_state.get("codebase_path", "./codebase"),
                "output_dir": output_dir,
                "dry_run": dry_run,
            }

            fixer_queue = Queue()
            fixer_result_store = {"fixer_status": "running"}

            st.session_state["fixer_log_queue"] = fixer_queue
            st.session_state["fixer_result_store"] = fixer_result_store
            st.session_state["fixer_logs"] = []
            st.session_state["fixer_phase_statuses"] = {i: "pending" for i in range(1, 5)}

            t = threading.Thread(
                target=run_fixer_background,
                args=(config, fixer_queue, fixer_result_store),
                daemon=True,
            )
            t.start()
            st.session_state["fixer_thread"] = t
            st.session_state["fixer_in_progress"] = True
            st.rerun()

    # ── Section 2: Execution log ──────────────────────────────────────────
    if fixer_in_progress:
        st.markdown("### 2. Execution Log")

        phases = FIXER_PHASES if WORKERS_AVAILABLE else {i: f"Phase {i}" for i in range(1, 5)}
        fixer_result_store = st.session_state.get("fixer_result_store", {})
        fixer_statuses = st.session_state.get("fixer_phase_statuses", {})

        if "fixer_phase_statuses" in fixer_result_store:
            fixer_statuses.update(fixer_result_store["fixer_phase_statuses"])
            st.session_state["fixer_phase_statuses"] = fixer_statuses

        st_tools.render_phase_tracker(phases, fixer_statuses)
        st.divider()

        # Drain fixer log queue
        fixer_queue = st.session_state.get("fixer_log_queue")
        if fixer_queue:
            done = _drain_log_queue(fixer_queue, st.session_state["fixer_logs"])
            if done:
                st.session_state["fixer_in_progress"] = False
                st.session_state["fixer_complete"] = True
                st.rerun()

        st_tools.render_log_stream(st.session_state.get("fixer_logs", []))
        time.sleep(0.5)
        st.rerun()

    # ── Section 3: QA Validation ──────────────────────────────────────────
    if fixer_complete:
        st.markdown("### 3. QA Validation Results")

        # Run QA if not already done
        if not st.session_state.get("qa_results") and QA_INSPECTOR_AVAILABLE:
            with st.spinner("Running QA validation..."):
                output_dir = st.session_state.get("output_dir", "./out")
                codebase_path = st.session_state.get("codebase_path", "./codebase")

                inspector = QAInspector(
                    fixed_codebase_path=codebase_path,
                    original_results=st.session_state.get("analysis_results", []),
                    original_metrics=st.session_state.get("analysis_metrics", {}),
                )
                qa_results = inspector.validate_all()
                st.session_state["qa_results"] = qa_results

        qa_results = st.session_state.get("qa_results", [])
        if qa_results:
            qa_df = pd.DataFrame(qa_results)
            st.dataframe(
                qa_df.style.apply(
                    lambda row: [
                        "background-color: #F0FDF4" if row.get("Pass") else "background-color: #FEF2F2"
                    ] * len(row),
                    axis=1,
                ),
                use_container_width=True,
                hide_index=True,
            )

            # Summary
            passed = sum(1 for r in qa_results if r.get("Pass"))
            failed = sum(1 for r in qa_results if not r.get("Pass"))
            st.markdown(
                f"**QA Summary:** {passed} checks passed, {failed} checks failed."
            )
        else:
            st.info("QA Inspector not available or no results.")

        st.divider()

        # ── Section 4: Traceability report ────────────────────────────────
        st.markdown("### 4. Traceability Report")

        feedback_df = st.session_state.get("feedback_df")
        fixer_result_store = st.session_state.get("fixer_result_store", {})
        fixer_results = fixer_result_store.get("fixer_results")
        audit_path = fixer_result_store.get("audit_report_path")

        if feedback_df is not None and FEEDBACK_HELPERS_AVAILABLE:
            trace_df = build_qa_traceability_report(
                feedback_df,
                fixer_results=fixer_results,
                audit_report_path=audit_path,
            )
            st.dataframe(trace_df, use_container_width=True, hide_index=True)

            # Downloads
            col1, col2, col3 = st.columns(3)
            with col1:
                trace_excel = export_to_excel_bytes(trace_df, sheet_name="QA Traceability")
                st.download_button(
                    "📥 Download QA Report (Excel)",
                    data=trace_excel,
                    file_name="cure_qa_traceability.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col2:
                trace_json = trace_df.to_json(orient="records", indent=2)
                st.download_button(
                    "📥 Download QA Report (JSON)",
                    data=trace_json,
                    file_name="cure_qa_traceability.json",
                    mime="application/json",
                )
            with col3:
                if QA_INSPECTOR_AVAILABLE:
                    codebase_path = st.session_state.get("codebase_path", "")
                    if codebase_path and os.path.isdir(codebase_path):
                        output_dir = st.session_state.get("output_dir", "./out")
                        zip_base = os.path.join(output_dir, "cure_fixed_codebase")
                        if st.button("📦 Create ZIP of Codebase"):
                            zip_path = create_zip_archive(codebase_path, zip_base)
                            with open(zip_path, "rb") as f:
                                st.download_button(
                                    "📥 Download ZIP",
                                    data=f.read(),
                                    file_name="cure_fixed_codebase.zip",
                                    mime="application/zip",
                                )
        else:
            st.caption("No feedback data available for traceability report.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: Chat (existing, preserved)
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
            f"<b>Welcome to <span style='color:{st_tools.CURE_PRIMARY};'>CURE</span> "
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

    for idx, (speaker, text) in enumerate(st.session_state.chat_history):
        role = "user" if speaker == "You" else "assistant"
        avatar = "🧑" if role == "user" else APP_ICON
        with st.chat_message(role, avatar=avatar):
            if role == "assistant":
                _render_markdown_with_tables(_extract_content(text))
            else:
                st.markdown(text)

            if role == "assistant":
                user_msg = ""
                if idx > 0 and st.session_state.chat_history[idx - 1][0] == "You":
                    user_msg = st.session_state.chat_history[idx - 1][1]
                feedback = st_tools.feedback_widget(idx, user_msg, text)
                if feedback:
                    st.session_state["all_feedback"].append(feedback)

    # Summarize old turns
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


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE: About
# ═══════════════════════════════════════════════════════════════════════════════

def page_about():
    """About page with project overview and connection info."""
    col1, col2 = st.columns([1, 3])
    with col1:
        if os.path.isfile(LOGO_SIDEBAR):
            st.image(LOGO_SIDEBAR, width=120)
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

    # FAQ content (merged from old page_faq)
    st.markdown("### Frequently Asked Questions")
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
            "```\n\n"
            "Or use the **Analyze** page in this dashboard to run analysis interactively.",
        ),
        (
            "What are deep static adapters?",
            "Adapters powered by real analysis tools instead of regex. "
            "Use `--enable-adapters` to activate Lizard (complexity), "
            "Flawfinder (security), and CCLS/libclang (dead code, call graphs, function metrics).",
        ),
    ]
    for q, a in FAQS:
        with st.expander(q):
            st.markdown(a)

    st.divider()
    net_ip = st_tools.get_local_ip()
    st.markdown(
        f"**Dashboard access:**  \n"
        f"This machine: [http://localhost:8502](http://localhost:8502)  \n"
        f"Network: [http://{net_ip}:8502](http://{net_ip}:8502)  \n\n"
        f"**Contact:** sendpavanr@gmail.com  \n"
        f"**Model:** `{STREAMLIT_MODEL}`"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    st_tools.app_css()

    # Header logo
    if os.path.isfile(LOGO_MAIN):
        st.image(LOGO_MAIN, width=480)

    # Sidebar navigation (returns "Workflow", "Chat", or "About")
    page = st_tools.sidebar(LOGO_SIDEBAR)

    if page == "Chat":
        page_chat()
    elif page == "About":
        page_about()
    else:
        # Workflow tabs — the four main workflow stages
        tab_analyze, tab_pipeline, tab_review, tab_fixqa = st.tabs(
            ["📊 Analyze", "⚙️ Pipeline", "📝 Review", "🔧 Fix & QA"]
        )
        with tab_analyze:
            page_analyze()
        with tab_pipeline:
            page_pipeline()
        with tab_review:
            page_review()
        with tab_fixqa:
            page_fixer_qa()


if __name__ == "__main__":
    main()
