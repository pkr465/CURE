"""
CURE — Codebase Update & Refactor Engine
Telemetry Service

Silent, fire-and-forget telemetry for tracking framework usage patterns.
Records analysis runs, fixer outcomes, LLM usage, and granular events
into PostgreSQL tables.

All public methods swallow exceptions so telemetry never blocks the pipeline.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class TelemetryService:
    """Silent telemetry collector backed by PostgreSQL.

    Usage::

        telemetry = TelemetryService(connection_string)
        run_id = telemetry.start_run(mode="analysis", codebase_path="/src")
        telemetry.log_event(run_id, "issue_found", file_path="foo.c", severity="high")
        telemetry.finish_run(run_id, status="completed", issues_total=42)
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        engine: Optional[Engine] = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self._engine: Optional[Engine] = None

        if not enabled:
            return

        try:
            if engine is not None:
                self._engine = engine
            elif connection_string:
                self._engine = create_engine(connection_string, pool_pre_ping=True)
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
                # Indexes
                for idx_sql in [
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_mode ON telemetry_runs(mode)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_runs_created ON telemetry_runs(created_at)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_run ON telemetry_events(run_id)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(event_type)",
                    "CREATE INDEX IF NOT EXISTS idx_telemetry_events_created ON telemetry_events(created_at)",
                ]:
                    conn.execute(text(idx_sql))
                conn.commit()
                logger.debug("TelemetryService: schema ready")
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
    # Event logging
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
    # Convenience shortcuts
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
        """Shortcut for logging an issue found."""
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
        """Shortcut for logging a fix outcome."""
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
        """Shortcut for logging an LLM API call."""
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
