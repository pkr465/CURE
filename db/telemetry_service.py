"""
CURE — Codebase Update & Refactor Engine
Telemetry Service

Silent, fire-and-forget telemetry for tracking framework usage patterns.
Records analysis runs, fixer outcomes, LLM usage, per-finding detail,
cost estimation, constraint effectiveness, and static analysis results
into PostgreSQL tables.

All public methods swallow exceptions so telemetry never blocks the pipeline.
"""

import logging
import time
import uuid
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Token Pricing (per 1M tokens: input, output)
# ═══════════════════════════════════════════════════════════════════════════════

_PRICING: Dict[str, Tuple[float, float]] = {
    # Anthropic
    "anthropic::claude-sonnet-4-20250514":  (3.0, 15.0),
    "anthropic::claude-opus-4-20250514":    (15.0, 75.0),
    "anthropic::claude-haiku-4-20250514":   (0.25, 1.25),
    # Vertex AI
    "vertexai::gemini-2.5-pro":             (1.25, 10.0),
    "vertexai::gemini-2.5-flash":           (0.15, 0.60),
    # Azure OpenAI
    "azure::gpt-4.1":                       (2.0, 8.0),
    "azure::gpt-4.1-mini":                  (0.40, 1.60),
    "azure::gpt-5.2":                       (5.0, 15.0),
    # QGenie (internal — zero cost)
    "qgenie::qwen2.5-14b-1m":              (0.0, 0.0),
}

_DEFAULT_PRICING: Tuple[float, float] = (3.0, 15.0)


def _estimate_cost(
    provider_model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate USD cost for an LLM call based on token counts."""
    input_rate, output_rate = _PRICING.get(provider_model, _DEFAULT_PRICING)
    cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
    return round(cost, 6)


class TelemetryService:
    """Silent telemetry collector backed by PostgreSQL.

    Usage::

        telemetry = TelemetryService(connection_string)
        run_id = telemetry.start_run(mode="analysis", codebase_path="/src")
        telemetry.log_finding(run_id, file_path="foo.c", title="NULL deref", severity="CRITICAL")
        telemetry.log_llm_call_detailed(run_id, provider="anthropic", model="claude-sonnet-4-20250514", ...)
        telemetry.finish_run(run_id, status="completed", issues_total=42)
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        engine: Optional[Engine] = None,
        enabled: bool = True,
        pool_size: int = 5,
        pool_recycle: int = 3600,
        pool_timeout: int = 30,
        pool_pre_ping: bool = True,
    ) -> None:
        self.enabled = enabled
        self._engine: Optional[Engine] = None

        if not enabled:
            return

        try:
            if engine is not None:
                self._engine = engine
            elif connection_string:
                self._engine = create_engine(
                    connection_string,
                    pool_size=pool_size,
                    pool_recycle=pool_recycle,
                    pool_timeout=pool_timeout,
                    pool_pre_ping=pool_pre_ping,
                )
            else:
                self.enabled = False
                logger.debug("TelemetryService disabled: no connection provided")
        except Exception as exc:
            self.enabled = False
            logger.debug("TelemetryService disabled: %s", exc)

        # Auto-create tables if engine is available
        if self._engine is not None:
            self._init_schema()

    # ------------------------------------------------------------------
    # Schema auto-creation
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create telemetry tables if they don't exist (seamless setup)."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_runs (
                        run_id              TEXT        PRIMARY KEY,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        finished_at         TIMESTAMPTZ,
                        mode                TEXT        NOT NULL,
                        status              TEXT        NOT NULL DEFAULT 'started',
                        codebase_path       TEXT,
                        files_analyzed      INTEGER     DEFAULT 0,
                        total_chunks        INTEGER     DEFAULT 0,
                        issues_total        INTEGER     DEFAULT 0,
                        issues_critical     INTEGER     DEFAULT 0,
                        issues_high         INTEGER     DEFAULT 0,
                        issues_medium       INTEGER     DEFAULT 0,
                        issues_low          INTEGER     DEFAULT 0,
                        issues_fixed        INTEGER     DEFAULT 0,
                        issues_skipped      INTEGER     DEFAULT 0,
                        issues_failed       INTEGER     DEFAULT 0,
                        llm_provider        TEXT,
                        llm_model           TEXT,
                        total_llm_calls     INTEGER     DEFAULT 0,
                        total_prompt_tokens  INTEGER    DEFAULT 0,
                        total_completion_tokens INTEGER DEFAULT 0,
                        total_llm_latency_ms INTEGER   DEFAULT 0,
                        use_ccls            BOOLEAN     DEFAULT FALSE,
                        use_hitl            BOOLEAN     DEFAULT FALSE,
                        constraints_used    TEXT,
                        duration_seconds    REAL,
                        metadata            JSONB
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_events (
                        event_id            BIGSERIAL   PRIMARY KEY,
                        run_id              TEXT        NOT NULL
                                             REFERENCES telemetry_runs(run_id)
                                             ON DELETE CASCADE,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        event_type          TEXT        NOT NULL,
                        file_path           TEXT,
                        line_number         INTEGER,
                        issue_type          TEXT,
                        severity            TEXT,
                        llm_provider        TEXT,
                        llm_model           TEXT,
                        prompt_tokens       INTEGER,
                        completion_tokens   INTEGER,
                        latency_ms          INTEGER,
                        detail              JSONB
                    )
                """))
                # New granular tables
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_findings (
                        finding_id          BIGSERIAL   PRIMARY KEY,
                        run_id              TEXT        NOT NULL
                                             REFERENCES telemetry_runs(run_id)
                                             ON DELETE CASCADE,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        file_path           TEXT,
                        line_start          INTEGER,
                        line_end            INTEGER,
                        title               TEXT,
                        category            TEXT,
                        severity            TEXT,
                        confidence          TEXT,
                        description         TEXT,
                        suggestion          TEXT,
                        code_snippet        TEXT,
                        fixed_code          TEXT,
                        is_false_positive   BOOLEAN     DEFAULT FALSE,
                        user_feedback       TEXT,
                        metadata            JSONB
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_llm_calls (
                        call_id             BIGSERIAL   PRIMARY KEY,
                        run_id              TEXT        NOT NULL
                                             REFERENCES telemetry_runs(run_id)
                                             ON DELETE CASCADE,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        provider            TEXT,
                        model               TEXT,
                        purpose             TEXT,
                        file_path           TEXT,
                        chunk_index         INTEGER,
                        prompt_tokens       INTEGER     DEFAULT 0,
                        completion_tokens   INTEGER     DEFAULT 0,
                        total_tokens        INTEGER     DEFAULT 0,
                        latency_ms          INTEGER     DEFAULT 0,
                        estimated_cost_usd  NUMERIC(10,6) DEFAULT 0,
                        status              TEXT        DEFAULT 'success',
                        error_message       TEXT,
                        metadata            JSONB
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_constraint_hits (
                        hit_id              BIGSERIAL   PRIMARY KEY,
                        run_id              TEXT        NOT NULL
                                             REFERENCES telemetry_runs(run_id)
                                             ON DELETE CASCADE,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        constraint_source   TEXT,
                        constraint_rule     TEXT,
                        file_path           TEXT,
                        issue_type          TEXT,
                        action              TEXT,
                        metadata            JSONB
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_static_analysis (
                        result_id           BIGSERIAL   PRIMARY KEY,
                        run_id              TEXT        NOT NULL
                                             REFERENCES telemetry_runs(run_id)
                                             ON DELETE CASCADE,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        adapter_name        TEXT,
                        file_path           TEXT,
                        findings_count      INTEGER     DEFAULT 0,
                        metrics             JSONB,
                        metadata            JSONB
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS telemetry_usage_reports (
                        report_id           BIGSERIAL   PRIMARY KEY,
                        report_date         DATE        NOT NULL,
                        report_type         TEXT        NOT NULL,
                        total_runs          INTEGER     DEFAULT 0,
                        total_files         INTEGER     DEFAULT 0,
                        total_findings      INTEGER     DEFAULT 0,
                        total_fixes         INTEGER     DEFAULT 0,
                        total_tokens        INTEGER     DEFAULT 0,
                        estimated_cost_usd  NUMERIC(10,4) DEFAULT 0,
                        top_issue_types     JSONB,
                        top_files           JSONB,
                        metadata            JSONB,
                        UNIQUE(report_date, report_type)
                    )
                """))
                # Indexes
                for idx_sql in [
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_mode ON telemetry_runs(mode)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_created ON telemetry_runs(created_at)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_run ON telemetry_events(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(event_type)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_created ON telemetry_events(created_at)",
                    "CREATE INDEX IF NOT EXISTS idx_findings_run ON telemetry_findings(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_findings_severity ON telemetry_findings(severity)",
                    "CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON telemetry_llm_calls(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_llm_calls_provider_model ON telemetry_llm_calls(provider, model)",
                    "CREATE INDEX IF NOT EXISTS idx_constraint_hits_run ON telemetry_constraint_hits(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_static_analysis_run ON telemetry_static_analysis(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_usage_reports_date ON telemetry_usage_reports(report_date)",
                ]:
                    conn.execute(text(idx_sql))
                conn.commit()
                logger.debug("TelemetryService: schema ready (7 tables)")
        except Exception as exc:
            logger.debug("TelemetryService: schema init failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        mode: str,
        codebase_path: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        use_ccls: bool = False,
        use_hitl: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Begin a new telemetry run.  Returns the run_id."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        if not self._safe_guard():
            return run_id

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_runs
                            (run_id, mode, status, codebase_path,
                             llm_provider, llm_model, use_ccls, use_hitl, metadata)
                        VALUES
                            (:run_id, :mode, 'started', :codebase_path,
                             :llm_provider, :llm_model, :use_ccls, :use_hitl,
                             :metadata::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "mode": mode,
                        "codebase_path": codebase_path,
                        "llm_provider": llm_provider,
                        "llm_model": llm_model,
                        "use_ccls": use_ccls,
                        "use_hitl": use_hitl,
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry start_run failed: %s", exc)

        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str = "completed",
        files_analyzed: int = 0,
        total_chunks: int = 0,
        issues_total: int = 0,
        issues_critical: int = 0,
        issues_high: int = 0,
        issues_medium: int = 0,
        issues_low: int = 0,
        issues_fixed: int = 0,
        issues_skipped: int = 0,
        issues_failed: int = 0,
        total_llm_calls: int = 0,
        total_prompt_tokens: int = 0,
        total_completion_tokens: int = 0,
        total_llm_latency_ms: int = 0,
        constraints_used: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Finalize a telemetry run with outcome data."""
        if not self._safe_guard():
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        UPDATE telemetry_runs SET
                            finished_at             = NOW(),
                            status                  = :status,
                            files_analyzed          = :files_analyzed,
                            total_chunks            = :total_chunks,
                            issues_total            = :issues_total,
                            issues_critical         = :issues_critical,
                            issues_high             = :issues_high,
                            issues_medium           = :issues_medium,
                            issues_low              = :issues_low,
                            issues_fixed            = :issues_fixed,
                            issues_skipped          = :issues_skipped,
                            issues_failed           = :issues_failed,
                            total_llm_calls         = :total_llm_calls,
                            total_prompt_tokens     = :total_prompt_tokens,
                            total_completion_tokens  = :total_completion_tokens,
                            total_llm_latency_ms    = :total_llm_latency_ms,
                            constraints_used        = :constraints_used,
                            duration_seconds        = :duration_seconds,
                            metadata                = COALESCE(:metadata::jsonb, metadata)
                        WHERE run_id = :run_id
                    """),
                    {
                        "run_id": run_id,
                        "status": status,
                        "files_analyzed": files_analyzed,
                        "total_chunks": total_chunks,
                        "issues_total": issues_total,
                        "issues_critical": issues_critical,
                        "issues_high": issues_high,
                        "issues_medium": issues_medium,
                        "issues_low": issues_low,
                        "issues_fixed": issues_fixed,
                        "issues_skipped": issues_skipped,
                        "issues_failed": issues_failed,
                        "total_llm_calls": total_llm_calls,
                        "total_prompt_tokens": total_prompt_tokens,
                        "total_completion_tokens": total_completion_tokens,
                        "total_llm_latency_ms": total_llm_latency_ms,
                        "constraints_used": constraints_used,
                        "duration_seconds": duration_seconds,
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry finish_run failed: %s", exc)

    # ------------------------------------------------------------------
    # Event logging (legacy — kept for backward compatibility)
    # ------------------------------------------------------------------

    def log_event(
        self,
        run_id: str,
        event_type: str,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        issue_type: Optional[str] = None,
        severity: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a granular event within a run."""
        if not self._safe_guard():
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_events
                            (run_id, event_type, file_path, line_number,
                             issue_type, severity,
                             llm_provider, llm_model, prompt_tokens,
                             completion_tokens, latency_ms, detail)
                        VALUES
                            (:run_id, :event_type, :file_path, :line_number,
                             :issue_type, :severity,
                             :llm_provider, :llm_model, :prompt_tokens,
                             :completion_tokens, :latency_ms, :detail::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "event_type": event_type,
                        "file_path": file_path,
                        "line_number": line_number,
                        "issue_type": issue_type,
                        "severity": severity,
                        "llm_provider": llm_provider,
                        "llm_model": llm_model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "latency_ms": latency_ms,
                        "detail": _to_json(detail),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry log_event failed: %s", exc)

    # ------------------------------------------------------------------
    # Convenience shortcuts (legacy — kept for backward compatibility)
    # ------------------------------------------------------------------

    def log_issue_found(
        self,
        run_id: str,
        file_path: str,
        issue_type: str,
        severity: str,
        line_number: Optional[int] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shortcut for logging an issue found (legacy events table)."""
        self.log_event(
            run_id=run_id,
            event_type="issue_found",
            file_path=file_path,
            issue_type=issue_type,
            severity=severity,
            line_number=line_number,
            detail=detail,
        )

    def log_fix_result(
        self,
        run_id: str,
        file_path: str,
        issue_type: str,
        result: str,  # 'fixed' | 'skipped' | 'failed'
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shortcut for logging a fix outcome (legacy events table)."""
        self.log_event(
            run_id=run_id,
            event_type=f"issue_{result}",
            file_path=file_path,
            issue_type=issue_type,
            detail=detail,
        )

    def log_llm_call(
        self,
        run_id: str,
        llm_provider: str,
        llm_model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shortcut for logging an LLM API call (legacy events table)."""
        self.log_event(
            run_id=run_id,
            event_type="llm_call",
            llm_provider=llm_provider,
            llm_model=llm_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            detail=detail,
        )

    def log_export(
        self,
        run_id: str,
        export_format: str,
        file_path: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shortcut for logging an export action."""
        self.log_event(
            run_id=run_id,
            event_type="export_action",
            file_path=file_path,
            detail={"format": export_format, **(detail or {})},
        )

    # ------------------------------------------------------------------
    # Granular logging — NEW tables
    # ------------------------------------------------------------------

    def log_finding(
        self,
        run_id: str,
        file_path: Optional[str] = None,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        title: Optional[str] = None,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        confidence: Optional[str] = None,
        description: Optional[str] = None,
        suggestion: Optional[str] = None,
        code_snippet: Optional[str] = None,
        fixed_code: Optional[str] = None,
        is_false_positive: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a detailed finding into telemetry_findings."""
        if not self._safe_guard():
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_findings
                            (run_id, file_path, line_start, line_end,
                             title, category, severity, confidence,
                             description, suggestion, code_snippet, fixed_code,
                             is_false_positive, metadata)
                        VALUES
                            (:run_id, :file_path, :line_start, :line_end,
                             :title, :category, :severity, :confidence,
                             :description, :suggestion, :code_snippet, :fixed_code,
                             :is_false_positive, :metadata::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_end,
                        "title": title,
                        "category": category,
                        "severity": severity,
                        "confidence": confidence,
                        "description": description,
                        "suggestion": suggestion,
                        "code_snippet": code_snippet,
                        "fixed_code": fixed_code,
                        "is_false_positive": is_false_positive,
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry log_finding failed: %s", exc)

    def log_llm_call_detailed(
        self,
        run_id: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        purpose: Optional[str] = None,
        file_path: Optional[str] = None,
        chunk_index: Optional[int] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        status: str = "success",
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a detailed LLM call into telemetry_llm_calls with cost estimation."""
        if not self._safe_guard():
            return

        total_tokens = prompt_tokens + completion_tokens
        provider_model = f"{provider}::{model}" if provider and model else ""
        cost = _estimate_cost(provider_model, prompt_tokens, completion_tokens)

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_llm_calls
                            (run_id, provider, model, purpose, file_path,
                             chunk_index, prompt_tokens, completion_tokens,
                             total_tokens, latency_ms, estimated_cost_usd,
                             status, error_message, metadata)
                        VALUES
                            (:run_id, :provider, :model, :purpose, :file_path,
                             :chunk_index, :prompt_tokens, :completion_tokens,
                             :total_tokens, :latency_ms, :estimated_cost_usd,
                             :status, :error_message, :metadata::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "provider": provider,
                        "model": model,
                        "purpose": purpose,
                        "file_path": file_path,
                        "chunk_index": chunk_index,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "latency_ms": latency_ms,
                        "estimated_cost_usd": cost,
                        "status": status,
                        "error_message": error_message,
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry log_llm_call_detailed failed: %s", exc)

    def log_constraint_hit(
        self,
        run_id: str,
        constraint_source: Optional[str] = None,
        constraint_rule: Optional[str] = None,
        file_path: Optional[str] = None,
        issue_type: Optional[str] = None,
        action: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a constraint application into telemetry_constraint_hits."""
        if not self._safe_guard():
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_constraint_hits
                            (run_id, constraint_source, constraint_rule,
                             file_path, issue_type, action, metadata)
                        VALUES
                            (:run_id, :constraint_source, :constraint_rule,
                             :file_path, :issue_type, :action, :metadata::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "constraint_source": constraint_source,
                        "constraint_rule": constraint_rule,
                        "file_path": file_path,
                        "issue_type": issue_type,
                        "action": action,
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry log_constraint_hit failed: %s", exc)

    def log_static_analysis(
        self,
        run_id: str,
        adapter_name: Optional[str] = None,
        file_path: Optional[str] = None,
        findings_count: int = 0,
        metrics: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log static analysis adapter results into telemetry_static_analysis."""
        if not self._safe_guard():
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO telemetry_static_analysis
                            (run_id, adapter_name, file_path,
                             findings_count, metrics, metadata)
                        VALUES
                            (:run_id, :adapter_name, :file_path,
                             :findings_count, :metrics::jsonb, :metadata::jsonb)
                    """),
                    {
                        "run_id": run_id,
                        "adapter_name": adapter_name,
                        "file_path": file_path,
                        "findings_count": findings_count,
                        "metrics": _to_json(metrics),
                        "metadata": _to_json(metadata),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Telemetry log_static_analysis failed: %s", exc)

    def generate_usage_report(
        self,
        report_date: Optional[date] = None,
        report_type: str = "daily",
    ) -> Optional[Dict[str, Any]]:
        """Aggregate data and upsert into telemetry_usage_reports."""
        if not self._safe_guard():
            return None

        if report_date is None:
            report_date = date.today()

        try:
            with self._engine.connect() as conn:
                # Determine date range
                if report_type == "weekly":
                    interval = "7 days"
                else:
                    interval = "1 day"

                # Aggregate from runs
                row = conn.execute(
                    text("""
                        SELECT
                            COUNT(*)                                AS total_runs,
                            COALESCE(SUM(files_analyzed), 0)        AS total_files,
                            COALESCE(SUM(issues_total), 0)          AS total_findings,
                            COALESCE(SUM(issues_fixed), 0)          AS total_fixes,
                            COALESCE(SUM(total_prompt_tokens + total_completion_tokens), 0) AS total_tokens
                        FROM telemetry_runs
                        WHERE DATE(created_at) >= :report_date - CAST(:interval AS INTERVAL)
                          AND DATE(created_at) <= :report_date
                    """),
                    {"report_date": report_date, "interval": interval},
                ).fetchone()

                if not row:
                    return None

                total_runs = row[0] or 0
                total_files = row[1] or 0
                total_findings = row[2] or 0
                total_fixes = row[3] or 0
                total_tokens = row[4] or 0

                # Estimated cost from llm_calls table
                cost_row = conn.execute(
                    text("""
                        SELECT COALESCE(SUM(estimated_cost_usd), 0)
                        FROM telemetry_llm_calls
                        WHERE DATE(created_at) >= :report_date - CAST(:interval AS INTERVAL)
                          AND DATE(created_at) <= :report_date
                    """),
                    {"report_date": report_date, "interval": interval},
                ).fetchone()
                estimated_cost = float(cost_row[0]) if cost_row else 0.0

                # Top issue types
                type_rows = conn.execute(
                    text("""
                        SELECT category, COUNT(*) AS cnt
                        FROM telemetry_findings
                        WHERE DATE(created_at) >= :report_date - CAST(:interval AS INTERVAL)
                          AND DATE(created_at) <= :report_date
                          AND category IS NOT NULL
                        GROUP BY category
                        ORDER BY cnt DESC
                        LIMIT 10
                    """),
                    {"report_date": report_date, "interval": interval},
                ).fetchall()
                top_issue_types = {r[0]: r[1] for r in type_rows}

                # Top files
                file_rows = conn.execute(
                    text("""
                        SELECT file_path, COUNT(*) AS cnt
                        FROM telemetry_findings
                        WHERE DATE(created_at) >= :report_date - CAST(:interval AS INTERVAL)
                          AND DATE(created_at) <= :report_date
                          AND file_path IS NOT NULL
                        GROUP BY file_path
                        ORDER BY cnt DESC
                        LIMIT 10
                    """),
                    {"report_date": report_date, "interval": interval},
                ).fetchall()
                top_files = {r[0]: r[1] for r in file_rows}

                # Upsert report
                conn.execute(
                    text("""
                        INSERT INTO telemetry_usage_reports
                            (report_date, report_type, total_runs, total_files,
                             total_findings, total_fixes, total_tokens,
                             estimated_cost_usd, top_issue_types, top_files)
                        VALUES
                            (:report_date, :report_type, :total_runs, :total_files,
                             :total_findings, :total_fixes, :total_tokens,
                             :estimated_cost_usd, :top_issue_types::jsonb, :top_files::jsonb)
                        ON CONFLICT (report_date, report_type) DO UPDATE SET
                            total_runs         = EXCLUDED.total_runs,
                            total_files        = EXCLUDED.total_files,
                            total_findings     = EXCLUDED.total_findings,
                            total_fixes        = EXCLUDED.total_fixes,
                            total_tokens       = EXCLUDED.total_tokens,
                            estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                            top_issue_types    = EXCLUDED.top_issue_types,
                            top_files          = EXCLUDED.top_files
                    """),
                    {
                        "report_date": report_date,
                        "report_type": report_type,
                        "total_runs": total_runs,
                        "total_files": total_files,
                        "total_findings": total_findings,
                        "total_fixes": total_fixes,
                        "total_tokens": total_tokens,
                        "estimated_cost_usd": estimated_cost,
                        "top_issue_types": _to_json(top_issue_types),
                        "top_files": _to_json(top_files),
                    },
                )
                conn.commit()

                return {
                    "report_date": str(report_date),
                    "report_type": report_type,
                    "total_runs": total_runs,
                    "total_files": total_files,
                    "total_findings": total_findings,
                    "total_fixes": total_fixes,
                    "total_tokens": total_tokens,
                    "estimated_cost_usd": estimated_cost,
                    "top_issue_types": top_issue_types,
                    "top_files": top_files,
                }
        except Exception as exc:
            logger.debug("Telemetry generate_usage_report failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Query API (for dashboards)
    # ------------------------------------------------------------------

    def get_recent_runs(self, limit: int = 50) -> list:
        """Return recent telemetry runs as dicts."""
        if not self._safe_guard():
            return []

        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM telemetry_runs
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Telemetry get_recent_runs failed: %s", exc)
            return []

    def get_run_events(self, run_id: str) -> list:
        """Return events for a specific run."""
        if not self._safe_guard():
            return []

        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM telemetry_events
                        WHERE run_id = :run_id
                        ORDER BY created_at ASC
                    """),
                    {"run_id": run_id},
                )
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Telemetry get_run_events failed: %s", exc)
            return []

    def get_summary_stats(self) -> Dict[str, Any]:
        """Return aggregate stats for the dashboard."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                # Run totals
                row = conn.execute(text("""
                    SELECT
                        COUNT(*)                              AS total_runs,
                        COUNT(*) FILTER (WHERE mode='analysis') AS analysis_runs,
                        COUNT(*) FILTER (WHERE mode='fixer')    AS fixer_runs,
                        COUNT(*) FILTER (WHERE mode='patch')    AS patch_runs,
                        COALESCE(SUM(issues_total), 0)          AS total_issues,
                        COALESCE(SUM(issues_fixed), 0)          AS total_fixed,
                        COALESCE(SUM(issues_skipped), 0)        AS total_skipped,
                        COALESCE(SUM(issues_failed), 0)         AS total_failed,
                        COALESCE(SUM(total_llm_calls), 0)       AS total_llm_calls,
                        COALESCE(SUM(total_prompt_tokens), 0)   AS total_prompt_tokens,
                        COALESCE(SUM(total_completion_tokens), 0) AS total_completion_tokens,
                        COALESCE(AVG(duration_seconds), 0)      AS avg_duration
                    FROM telemetry_runs
                """)).fetchone()

                stats = dict(zip(row._mapping.keys(), row)) if row else {}

                # Issues by severity across all runs
                sev_rows = conn.execute(text("""
                    SELECT severity, COUNT(*) as count
                    FROM telemetry_events
                    WHERE event_type = 'issue_found' AND severity IS NOT NULL
                    GROUP BY severity
                    ORDER BY count DESC
                """)).fetchall()
                stats["issues_by_severity"] = {r[0]: r[1] for r in sev_rows}

                # Top issue types
                type_rows = conn.execute(text("""
                    SELECT issue_type, COUNT(*) as count
                    FROM telemetry_events
                    WHERE event_type = 'issue_found' AND issue_type IS NOT NULL
                    GROUP BY issue_type
                    ORDER BY count DESC
                    LIMIT 20
                """)).fetchall()
                stats["top_issue_types"] = {r[0]: r[1] for r in type_rows}

                # Runs over time (last 30 days, grouped by date)
                time_rows = conn.execute(text("""
                    SELECT DATE(created_at) AS run_date, COUNT(*) AS count
                    FROM telemetry_runs
                    WHERE created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY run_date
                """)).fetchall()
                stats["runs_by_date"] = {str(r[0]): r[1] for r in time_rows}

                # Fix success rate
                total_attempted = (
                    stats.get("total_fixed", 0)
                    + stats.get("total_failed", 0)
                )
                stats["fix_success_rate"] = (
                    round(stats.get("total_fixed", 0) / total_attempted * 100, 1)
                    if total_attempted > 0
                    else 0.0
                )

                return stats
        except Exception as exc:
            logger.debug("Telemetry get_summary_stats failed: %s", exc)
            return {}

    def get_llm_usage_stats(self) -> Dict[str, Any]:
        """Return LLM usage stats grouped by provider/model."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT
                        llm_provider,
                        llm_model,
                        COUNT(*)                    AS call_count,
                        SUM(prompt_tokens)          AS total_prompt_tokens,
                        SUM(completion_tokens)      AS total_completion_tokens,
                        AVG(latency_ms)             AS avg_latency_ms
                    FROM telemetry_events
                    WHERE event_type = 'llm_call'
                      AND llm_provider IS NOT NULL
                    GROUP BY llm_provider, llm_model
                    ORDER BY call_count DESC
                """)).fetchall()

                return {
                    "by_model": [
                        {
                            "provider": r[0],
                            "model": r[1],
                            "calls": r[2],
                            "prompt_tokens": r[3] or 0,
                            "completion_tokens": r[4] or 0,
                            "avg_latency_ms": round(r[5] or 0, 1),
                        }
                        for r in rows
                    ]
                }
        except Exception as exc:
            logger.debug("Telemetry get_llm_usage_stats failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # New query APIs (for enhanced dashboard)
    # ------------------------------------------------------------------

    def get_cost_summary(self, days: int = 30) -> Dict[str, Any]:
        """Return cost breakdown by provider/model and daily trend."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                # Cost by provider/model
                model_rows = conn.execute(text("""
                    SELECT
                        provider, model,
                        COUNT(*)                            AS call_count,
                        SUM(prompt_tokens)                  AS total_prompt_tokens,
                        SUM(completion_tokens)              AS total_completion_tokens,
                        SUM(estimated_cost_usd)             AS total_cost_usd,
                        AVG(latency_ms)                     AS avg_latency_ms
                    FROM telemetry_llm_calls
                    WHERE created_at >= NOW() - CAST(:days || ' days' AS INTERVAL)
                    GROUP BY provider, model
                    ORDER BY total_cost_usd DESC
                """), {"days": days}).fetchall()

                by_model = [
                    {
                        "provider": r[0], "model": r[1],
                        "calls": r[2],
                        "prompt_tokens": int(r[3] or 0),
                        "completion_tokens": int(r[4] or 0),
                        "total_cost_usd": float(r[5] or 0),
                        "avg_latency_ms": round(float(r[6] or 0), 1),
                    }
                    for r in model_rows
                ]

                # Daily cost trend
                daily_rows = conn.execute(text("""
                    SELECT
                        DATE(created_at) AS day,
                        SUM(estimated_cost_usd) AS daily_cost
                    FROM telemetry_llm_calls
                    WHERE created_at >= NOW() - CAST(:days || ' days' AS INTERVAL)
                    GROUP BY DATE(created_at)
                    ORDER BY day
                """), {"days": days}).fetchall()

                daily_trend = {str(r[0]): float(r[1] or 0) for r in daily_rows}

                total_cost = sum(m["total_cost_usd"] for m in by_model)

                return {
                    "total_cost_usd": round(total_cost, 4),
                    "by_model": by_model,
                    "daily_trend": daily_trend,
                }
        except Exception as exc:
            logger.debug("Telemetry get_cost_summary failed: %s", exc)
            return {}

    def get_findings_detail(self, run_id: str) -> list:
        """Return all findings for a specific run."""
        if not self._safe_guard():
            return []

        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM telemetry_findings
                        WHERE run_id = :run_id
                        ORDER BY created_at ASC
                    """),
                    {"run_id": run_id},
                )
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Telemetry get_findings_detail failed: %s", exc)
            return []

    def get_constraint_effectiveness(
        self, run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return constraint hit counts grouped by rule and action."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                where_clause = "WHERE run_id = :run_id" if run_id else ""
                params = {"run_id": run_id} if run_id else {}

                rows = conn.execute(
                    text(f"""
                        SELECT
                            constraint_rule,
                            action,
                            COUNT(*) AS hit_count
                        FROM telemetry_constraint_hits
                        {where_clause}
                        GROUP BY constraint_rule, action
                        ORDER BY hit_count DESC
                    """),
                    params,
                ).fetchall()

                by_rule: Dict[str, Dict[str, int]] = {}
                for r in rows:
                    rule = r[0] or "unknown"
                    action = r[1] or "unknown"
                    if rule not in by_rule:
                        by_rule[rule] = {}
                    by_rule[rule][action] = r[2]

                total_suppressions = sum(
                    r[2] for r in rows if r[1] in ("suppressed", "hitl_suppressed")
                )

                return {
                    "by_rule": by_rule,
                    "total_hits": sum(r[2] for r in rows),
                    "total_suppressions": total_suppressions,
                }
        except Exception as exc:
            logger.debug("Telemetry get_constraint_effectiveness failed: %s", exc)
            return {}

    def get_false_positive_rate(self, days: int = 30) -> Dict[str, Any]:
        """Return false positive rate from telemetry_findings."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT
                        COUNT(*) AS total_findings,
                        COUNT(*) FILTER (WHERE is_false_positive = TRUE) AS false_positives
                    FROM telemetry_findings
                    WHERE created_at >= NOW() - CAST(:days || ' days' AS INTERVAL)
                """), {"days": days}).fetchone()

                total = row[0] or 0
                fp = row[1] or 0
                rate = round(fp / total * 100, 1) if total > 0 else 0.0

                return {
                    "total_findings": total,
                    "false_positives": fp,
                    "false_positive_rate": rate,
                }
        except Exception as exc:
            logger.debug("Telemetry get_false_positive_rate failed: %s", exc)
            return {}

    def get_agent_comparison(self, days: int = 30) -> Dict[str, Any]:
        """Return side-by-side stats for analysis vs patch vs fixer modes."""
        if not self._safe_guard():
            return {}

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT
                        mode,
                        COUNT(*)                              AS run_count,
                        COALESCE(SUM(issues_total), 0)        AS total_issues,
                        COALESCE(SUM(issues_fixed), 0)        AS total_fixed,
                        COALESCE(AVG(duration_seconds), 0)    AS avg_duration,
                        COALESCE(SUM(total_llm_calls), 0)     AS total_llm_calls,
                        COALESCE(SUM(total_prompt_tokens + total_completion_tokens), 0) AS total_tokens
                    FROM telemetry_runs
                    WHERE created_at >= NOW() - CAST(:days || ' days' AS INTERVAL)
                    GROUP BY mode
                    ORDER BY run_count DESC
                """), {"days": days}).fetchall()

                return {
                    "by_mode": [
                        {
                            "mode": r[0],
                            "run_count": r[1],
                            "total_issues": int(r[2]),
                            "total_fixed": int(r[3]),
                            "avg_duration": round(float(r[4]), 1),
                            "total_llm_calls": int(r[5]),
                            "total_tokens": int(r[6]),
                        }
                        for r in rows
                    ]
                }
        except Exception as exc:
            logger.debug("Telemetry get_agent_comparison failed: %s", exc)
            return {}

    def get_usage_reports(
        self, report_type: str = "daily", limit: int = 30
    ) -> list:
        """Retrieve materialized usage reports."""
        if not self._safe_guard():
            return []

        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM telemetry_usage_reports
                        WHERE report_type = :report_type
                        ORDER BY report_date DESC
                        LIMIT :limit
                    """),
                    {"report_type": report_type, "limit": limit},
                )
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as exc:
            logger.debug("Telemetry get_usage_reports failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _safe_guard(self) -> bool:
        """Return True if telemetry is enabled and engine is available."""
        return self.enabled and self._engine is not None


def _to_json(obj: Any) -> Optional[str]:
    """Safely serialize to JSON string or return None."""
    if obj is None:
        return None
    import json
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return None
