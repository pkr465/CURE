"""
CURE — Codebase Update & Refactor Engine
HITL Feedback Store

PostgreSQL-backed persistent store for human feedback decisions and constraint
rules.  Survives across analysis runs so agents can learn from accumulated
human wisdom.

Migrated from SQLite to PostgreSQL to share the existing codebase_analytics_db.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import HITLConfig
from .schemas import ConstraintRule, FeedbackDecision

logger = logging.getLogger(__name__)


class FeedbackStore:
    """Persistent storage for HITL feedback decisions and constraint rules.

    Uses PostgreSQL (shared ``codebase_analytics_db``).  Three tables:

    * ``hitl_feedback_decisions`` — human review outcomes
    * ``hitl_constraint_rules``  — parsed constraint markdown rules
    * ``hitl_run_metadata``      — audit trail of analysis runs
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        connection_string: Optional[str] = None,
        engine: Optional[Engine] = None,
        config: Optional[HITLConfig] = None,
        db_path: Optional[str] = None,  # kept for backward compat, ignored
    ) -> None:
        self.config = config or HITLConfig()
        self._skip_cache: Optional[Set[Tuple[str, str]]] = None
        self._engine: Optional[Engine] = None

        # Build engine from connection string or reuse existing
        if engine is not None:
            self._engine = engine
        elif connection_string:
            try:
                self._engine = create_engine(connection_string, pool_pre_ping=True)
            except Exception as exc:
                logger.error("FeedbackStore: failed to create engine: %s", exc)
        else:
            # Try to load from GlobalConfig
            self._engine = self._engine_from_config()

        if self._engine is not None:
            self._init_schema()
            logger.info("FeedbackStore initialised (PostgreSQL)")
        else:
            logger.warning(
                "FeedbackStore: no database connection — running in read-only/noop mode"
            )

    # ------------------------------------------------------------------
    # Config-based engine creation
    # ------------------------------------------------------------------

    @staticmethod
    def _engine_from_config() -> Optional[Engine]:
        """Attempt to create an engine from GlobalConfig."""
        try:
            from utils.parsers.global_config_parser import GlobalConfig
            gc = GlobalConfig()
            conn_str = gc.get("POSTGRES_CONNECTION")
            if conn_str:
                return create_engine(conn_str, pool_pre_ping=True)
        except Exception as exc:
            logger.debug("FeedbackStore: could not load GlobalConfig: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they do not already exist."""
        if self._engine is None:
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS hitl_feedback_decisions (
                        id                  TEXT PRIMARY KEY,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        source              TEXT        NOT NULL,
                        file_path           TEXT        NOT NULL,
                        line_number         INTEGER,
                        code_snippet        TEXT,
                        issue_type          TEXT,
                        severity            TEXT,
                        human_action        TEXT        NOT NULL,
                        human_feedback_text TEXT,
                        applied_constraints JSONB,
                        remediation_notes   TEXT,
                        agent_that_flagged  TEXT,
                        run_id              TEXT
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS hitl_constraint_rules (
                        rule_id               TEXT PRIMARY KEY,
                        description           TEXT,
                        standard_remediation  TEXT,
                        llm_action            TEXT,
                        reasoning             TEXT,
                        example_allowed       TEXT,
                        example_prohibited    TEXT,
                        applies_to_patterns   JSONB,
                        source_file           TEXT
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS hitl_run_metadata (
                        run_id           TEXT        PRIMARY KEY,
                        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        config_snapshot  JSONB
                    )
                """))
                # Indexes
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_hitl_fd_issue_type
                    ON hitl_feedback_decisions(issue_type)
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_hitl_fd_file_path
                    ON hitl_feedback_decisions(file_path)
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_hitl_fd_human_action
                    ON hitl_feedback_decisions(human_action)
                """))
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore: schema init failed: %s", exc)

    # ------------------------------------------------------------------
    # Write API — decisions
    # ------------------------------------------------------------------

    def save_decision(self, decision: FeedbackDecision) -> None:
        """Persist (or update) a single feedback decision."""
        if self._engine is None:
            return

        constraints_json = (
            json.dumps(decision.applied_constraints)
            if decision.applied_constraints
            else None
        )

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO hitl_feedback_decisions
                            (id, created_at, source, file_path, line_number,
                             code_snippet, issue_type, severity, human_action,
                             human_feedback_text, applied_constraints,
                             remediation_notes, agent_that_flagged, run_id)
                        VALUES
                            (:id, :ts, :source, :file_path, :line_number,
                             :code_snippet, :issue_type, :severity, :human_action,
                             :human_feedback_text, :applied_constraints::jsonb,
                             :remediation_notes, :agent_that_flagged, :run_id)
                        ON CONFLICT (id) DO UPDATE SET
                            created_at          = EXCLUDED.created_at,
                            source              = EXCLUDED.source,
                            file_path           = EXCLUDED.file_path,
                            line_number         = EXCLUDED.line_number,
                            code_snippet        = EXCLUDED.code_snippet,
                            issue_type          = EXCLUDED.issue_type,
                            severity            = EXCLUDED.severity,
                            human_action        = EXCLUDED.human_action,
                            human_feedback_text = EXCLUDED.human_feedback_text,
                            applied_constraints = EXCLUDED.applied_constraints,
                            remediation_notes   = EXCLUDED.remediation_notes,
                            agent_that_flagged  = EXCLUDED.agent_that_flagged,
                            run_id              = EXCLUDED.run_id
                    """),
                    {
                        "id": decision.id,
                        "ts": decision.timestamp.isoformat(),
                        "source": decision.source,
                        "file_path": decision.file_path,
                        "line_number": decision.line_number,
                        "code_snippet": decision.code_snippet,
                        "issue_type": decision.issue_type,
                        "severity": decision.severity,
                        "human_action": decision.human_action,
                        "human_feedback_text": decision.human_feedback_text,
                        "applied_constraints": constraints_json,
                        "remediation_notes": decision.remediation_notes,
                        "agent_that_flagged": decision.agent_that_flagged,
                        "run_id": decision.run_id,
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore save_decision failed: %s", exc)

        self._skip_cache = None  # invalidate cache

    def bulk_save_decisions(self, decisions: List[FeedbackDecision]) -> None:
        """Persist many decisions in a single transaction."""
        if self._engine is None:
            return

        try:
            with self._engine.connect() as conn:
                for d in decisions:
                    constraints_json = (
                        json.dumps(d.applied_constraints)
                        if d.applied_constraints
                        else None
                    )
                    conn.execute(
                        text("""
                            INSERT INTO hitl_feedback_decisions
                                (id, created_at, source, file_path, line_number,
                                 code_snippet, issue_type, severity, human_action,
                                 human_feedback_text, applied_constraints,
                                 remediation_notes, agent_that_flagged, run_id)
                            VALUES
                                (:id, :ts, :source, :file_path, :line_number,
                                 :code_snippet, :issue_type, :severity, :human_action,
                                 :human_feedback_text, :applied_constraints::jsonb,
                                 :remediation_notes, :agent_that_flagged, :run_id)
                            ON CONFLICT (id) DO UPDATE SET
                                human_action        = EXCLUDED.human_action,
                                human_feedback_text = EXCLUDED.human_feedback_text,
                                applied_constraints = EXCLUDED.applied_constraints,
                                remediation_notes   = EXCLUDED.remediation_notes
                        """),
                        {
                            "id": d.id,
                            "ts": d.timestamp.isoformat(),
                            "source": d.source,
                            "file_path": d.file_path,
                            "line_number": d.line_number,
                            "code_snippet": d.code_snippet,
                            "issue_type": d.issue_type,
                            "severity": d.severity,
                            "human_action": d.human_action,
                            "human_feedback_text": d.human_feedback_text,
                            "applied_constraints": constraints_json,
                            "remediation_notes": d.remediation_notes,
                            "agent_that_flagged": d.agent_that_flagged,
                            "run_id": d.run_id,
                        },
                    )
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore bulk_save_decisions failed: %s", exc)

        self._skip_cache = None
        logger.info("Bulk-saved %d decisions", len(decisions))

    # ------------------------------------------------------------------
    # Write API — constraint rules
    # ------------------------------------------------------------------

    def save_constraint_rule(self, rule: ConstraintRule) -> None:
        """Persist (or update) a constraint rule."""
        if self._engine is None:
            return

        patterns_json = (
            json.dumps(rule.applies_to_patterns)
            if rule.applies_to_patterns
            else None
        )

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO hitl_constraint_rules
                            (rule_id, description, standard_remediation, llm_action,
                             reasoning, example_allowed, example_prohibited,
                             applies_to_patterns, source_file)
                        VALUES
                            (:rule_id, :description, :standard_remediation, :llm_action,
                             :reasoning, :example_allowed, :example_prohibited,
                             :applies_to_patterns::jsonb, :source_file)
                        ON CONFLICT (rule_id) DO UPDATE SET
                            description          = EXCLUDED.description,
                            standard_remediation = EXCLUDED.standard_remediation,
                            llm_action           = EXCLUDED.llm_action,
                            reasoning            = EXCLUDED.reasoning,
                            example_allowed      = EXCLUDED.example_allowed,
                            example_prohibited   = EXCLUDED.example_prohibited,
                            applies_to_patterns  = EXCLUDED.applies_to_patterns,
                            source_file          = EXCLUDED.source_file
                    """),
                    {
                        "rule_id": rule.rule_id,
                        "description": rule.description,
                        "standard_remediation": rule.standard_remediation,
                        "llm_action": rule.llm_action,
                        "reasoning": rule.reasoning,
                        "example_allowed": rule.example_allowed,
                        "example_prohibited": rule.example_prohibited,
                        "applies_to_patterns": patterns_json,
                        "source_file": rule.source_file,
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore save_constraint_rule failed: %s", exc)

    # ------------------------------------------------------------------
    # Write API — run metadata
    # ------------------------------------------------------------------

    def save_run_metadata(
        self, run_id: str, config_snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        """Record metadata for an analysis run (audit trail)."""
        if self._engine is None:
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO hitl_run_metadata
                            (run_id, config_snapshot)
                        VALUES
                            (:run_id, :config_snapshot::jsonb)
                        ON CONFLICT (run_id) DO UPDATE SET
                            config_snapshot = EXCLUDED.config_snapshot
                    """),
                    {
                        "run_id": run_id,
                        "config_snapshot": (
                            json.dumps(config_snapshot) if config_snapshot else None
                        ),
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore save_run_metadata failed: %s", exc)

    # ------------------------------------------------------------------
    # Read API — decisions
    # ------------------------------------------------------------------

    def get_all_decisions(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[FeedbackDecision]:
        """Return all decisions, optionally filtered by field values."""
        if self._engine is None:
            return []

        query = "SELECT * FROM hitl_feedback_decisions WHERE 1=1"
        params: Dict[str, Any] = {}

        if filters:
            if "issue_type" in filters:
                query += " AND issue_type = :issue_type"
                params["issue_type"] = filters["issue_type"]
            if "file_path" in filters:
                query += " AND file_path LIKE :file_path"
                params["file_path"] = f"%{filters['file_path']}%"
            if "human_action" in filters:
                query += " AND human_action = :human_action"
                params["human_action"] = filters["human_action"]
            if "severity" in filters:
                query += " AND severity = :severity"
                params["severity"] = filters["severity"]
            if "source" in filters:
                query += " AND source = :source"
                params["source"] = filters["source"]

        query += " ORDER BY created_at DESC"

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text(query), params).fetchall()
                columns = [
                    "id", "created_at", "source", "file_path", "line_number",
                    "code_snippet", "issue_type", "severity", "human_action",
                    "human_feedback_text", "applied_constraints",
                    "remediation_notes", "agent_that_flagged", "run_id",
                ]
                return [
                    self._row_to_decision(dict(zip(columns, row)))
                    for row in rows
                ]
        except Exception as exc:
            logger.error("FeedbackStore get_all_decisions failed: %s", exc)
            return []

    def get_decisions_by_issue_type(
        self, issue_type: str
    ) -> List[FeedbackDecision]:
        """Retrieve decisions matching a specific issue type."""
        return self.get_all_decisions(filters={"issue_type": issue_type})

    def get_decisions_by_file(
        self, file_path_pattern: str
    ) -> List[FeedbackDecision]:
        """Retrieve decisions whose file_path contains *file_path_pattern*."""
        return self.get_all_decisions(filters={"file_path": file_path_pattern})

    def get_decision_by_id(self, decision_id: str) -> Optional[FeedbackDecision]:
        """Retrieve a single decision by its UUID."""
        if self._engine is None:
            return None

        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM hitl_feedback_decisions WHERE id = :id"),
                    {"id": decision_id},
                ).fetchone()

            if not row:
                return None

            columns = [
                "id", "created_at", "source", "file_path", "line_number",
                "code_snippet", "issue_type", "severity", "human_action",
                "human_feedback_text", "applied_constraints",
                "remediation_notes", "agent_that_flagged", "run_id",
            ]
            return self._row_to_decision(dict(zip(columns, row)))
        except Exception as exc:
            logger.error("FeedbackStore get_decision_by_id failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Read API — constraint rules
    # ------------------------------------------------------------------

    def get_all_constraint_rules(self) -> List[ConstraintRule]:
        """Return every cached constraint rule."""
        if self._engine is None:
            return []

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT * FROM hitl_constraint_rules")
                ).fetchall()

            columns = [
                "rule_id", "description", "standard_remediation", "llm_action",
                "reasoning", "example_allowed", "example_prohibited",
                "applies_to_patterns", "source_file",
            ]
            return [
                self._row_to_constraint_rule(dict(zip(columns, row)))
                for row in rows
            ]
        except Exception as exc:
            logger.error("FeedbackStore get_all_constraint_rules failed: %s", exc)
            return []

    def get_constraint_rule(self, rule_id: str) -> Optional[ConstraintRule]:
        """Retrieve a specific constraint rule by ID."""
        if self._engine is None:
            return None

        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM hitl_constraint_rules WHERE rule_id = :rule_id"),
                    {"rule_id": rule_id},
                ).fetchone()

            if not row:
                return None

            columns = [
                "rule_id", "description", "standard_remediation", "llm_action",
                "reasoning", "example_allowed", "example_prohibited",
                "applies_to_patterns", "source_file",
            ]
            return self._row_to_constraint_rule(dict(zip(columns, row)))
        except Exception as exc:
            logger.error("FeedbackStore get_constraint_rule failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Fast skip check
    # ------------------------------------------------------------------

    def get_skip_set(self) -> Set[Tuple[str, str]]:
        """Return a cached set of ``(issue_type, file_path)`` pairs marked SKIP.

        The cache is invalidated whenever new decisions are saved.
        """
        if self._skip_cache is not None:
            return self._skip_cache

        if self._engine is None:
            return set()

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT issue_type, file_path FROM hitl_feedback_decisions "
                        "WHERE human_action = 'SKIP'"
                    )
                ).fetchall()
            self._skip_cache = {(r[0], r[1]) for r in rows}
        except Exception as exc:
            logger.error("FeedbackStore get_skip_set failed: %s", exc)
            self._skip_cache = set()

        return self._skip_cache

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        """Return aggregate statistics about the store."""
        if self._engine is None:
            return {"total_decisions": 0, "total_constraints": 0, "actions_breakdown": {}}

        try:
            with self._engine.connect() as conn:
                total = conn.execute(
                    text("SELECT COUNT(*) FROM hitl_feedback_decisions")
                ).fetchone()[0]

                actions = conn.execute(
                    text(
                        "SELECT human_action, COUNT(*) FROM hitl_feedback_decisions "
                        "GROUP BY human_action"
                    )
                ).fetchall()

                rules_count = conn.execute(
                    text("SELECT COUNT(*) FROM hitl_constraint_rules")
                ).fetchone()[0]

            return {
                "total_decisions": total,
                "total_constraints": rules_count,
                "actions_breakdown": {a: c for a, c in actions},
            }
        except Exception as exc:
            logger.error("FeedbackStore get_statistics failed: %s", exc)
            return {"total_decisions": 0, "total_constraints": 0, "actions_breakdown": {}}

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Delete all data.  Use only for testing."""
        if self._engine is None:
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(text("DELETE FROM hitl_feedback_decisions"))
                conn.execute(text("DELETE FROM hitl_constraint_rules"))
                conn.execute(text("DELETE FROM hitl_run_metadata"))
                conn.commit()
        except Exception as exc:
            logger.error("FeedbackStore clear_all failed: %s", exc)

        self._skip_cache = None
        logger.warning("Cleared all HITL store data")

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_decision(row: Dict[str, Any]) -> FeedbackDecision:
        constraints_raw = row.get("applied_constraints")
        # JSONB columns come back as dict already; TEXT columns need json.loads
        if isinstance(constraints_raw, str):
            try:
                constraints_raw = json.loads(constraints_raw)
            except (json.JSONDecodeError, TypeError):
                constraints_raw = None

        timestamp = row.get("created_at") or row.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        return FeedbackDecision(
            id=row["id"],
            timestamp=timestamp,
            source=row["source"],
            file_path=row["file_path"],
            line_number=row.get("line_number"),
            code_snippet=row.get("code_snippet"),
            issue_type=row.get("issue_type") or "",
            severity=row.get("severity") or "medium",
            human_action=row["human_action"],
            human_feedback_text=row.get("human_feedback_text"),
            applied_constraints=constraints_raw,
            remediation_notes=row.get("remediation_notes"),
            agent_that_flagged=row.get("agent_that_flagged"),
            run_id=row.get("run_id"),
        )

    @staticmethod
    def _row_to_constraint_rule(row: Dict[str, Any]) -> ConstraintRule:
        patterns_raw = row.get("applies_to_patterns")
        # JSONB columns come back as list already; TEXT columns need json.loads
        if isinstance(patterns_raw, str):
            try:
                patterns_raw = json.loads(patterns_raw)
            except (json.JSONDecodeError, TypeError):
                patterns_raw = None

        return ConstraintRule(
            rule_id=row["rule_id"],
            description=row.get("description") or "",
            standard_remediation=row.get("standard_remediation") or "",
            llm_action=row.get("llm_action") or "",
            reasoning=row.get("reasoning"),
            example_allowed=row.get("example_allowed"),
            example_prohibited=row.get("example_prohibited"),
            applies_to_patterns=patterns_raw,
            source_file=row.get("source_file"),
        )
