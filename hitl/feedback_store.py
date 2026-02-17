"""
CURE — Codebase Update & Refactor Engine
HITL Feedback Store

SQLite-backed persistent store for human feedback decisions and constraint
rules.  Survives across analysis runs so agents can learn from accumulated
human wisdom.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import HITLConfig
from .schemas import ConstraintRule, FeedbackDecision

logger = logging.getLogger(__name__)


class FeedbackStore:
    """Persistent storage for HITL feedback decisions and constraint rules.

    Uses SQLite for zero-dependency persistence.  Three tables:

    * ``feedback_decisions`` — human review outcomes
    * ``constraint_rules``  — parsed constraint markdown rules
    * ``run_metadata``      — audit trail of analysis runs
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, db_path: str, config: Optional[HITLConfig] = None) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config or HITLConfig()
        self._skip_cache: Optional[Set[Tuple[str, str]]] = None
        self._init_schema()
        logger.info("FeedbackStore initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback_decisions (
                    id                  TEXT PRIMARY KEY,
                    timestamp           TEXT    NOT NULL,
                    source              TEXT    NOT NULL,
                    file_path           TEXT    NOT NULL,
                    line_number         INTEGER,
                    code_snippet        TEXT,
                    issue_type          TEXT,
                    severity            TEXT,
                    human_action        TEXT    NOT NULL,
                    human_feedback_text TEXT,
                    applied_constraints TEXT,
                    remediation_notes   TEXT,
                    agent_that_flagged  TEXT,
                    run_id              TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS constraint_rules (
                    rule_id               TEXT PRIMARY KEY,
                    description           TEXT,
                    standard_remediation  TEXT,
                    llm_action            TEXT,
                    reasoning             TEXT,
                    example_allowed       TEXT,
                    example_prohibited    TEXT,
                    applies_to_patterns   TEXT,
                    source_file           TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS run_metadata (
                    run_id           TEXT PRIMARY KEY,
                    timestamp        TEXT NOT NULL,
                    config_snapshot  TEXT
                )
            """)
            # Indices for common queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_fd_issue_type
                ON feedback_decisions(issue_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_fd_file_path
                ON feedback_decisions(file_path)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_fd_human_action
                ON feedback_decisions(human_action)
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Connection helper
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    # ------------------------------------------------------------------
    # Write API — decisions
    # ------------------------------------------------------------------

    def save_decision(self, decision: FeedbackDecision) -> None:
        """Persist (or update) a single feedback decision."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO feedback_decisions
                    (id, timestamp, source, file_path, line_number, code_snippet,
                     issue_type, severity, human_action, human_feedback_text,
                     applied_constraints, remediation_notes, agent_that_flagged,
                     run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.timestamp.isoformat(),
                    decision.source,
                    decision.file_path,
                    decision.line_number,
                    decision.code_snippet,
                    decision.issue_type,
                    decision.severity,
                    decision.human_action,
                    decision.human_feedback_text,
                    json.dumps(decision.applied_constraints)
                    if decision.applied_constraints
                    else None,
                    decision.remediation_notes,
                    decision.agent_that_flagged,
                    decision.run_id,
                ),
            )
            conn.commit()
        self._skip_cache = None  # invalidate cache

    def bulk_save_decisions(self, decisions: List[FeedbackDecision]) -> None:
        """Persist many decisions in a single transaction."""
        with self._connect() as conn:
            for d in decisions:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feedback_decisions
                        (id, timestamp, source, file_path, line_number,
                         code_snippet, issue_type, severity, human_action,
                         human_feedback_text, applied_constraints,
                         remediation_notes, agent_that_flagged, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.id,
                        d.timestamp.isoformat(),
                        d.source,
                        d.file_path,
                        d.line_number,
                        d.code_snippet,
                        d.issue_type,
                        d.severity,
                        d.human_action,
                        d.human_feedback_text,
                        json.dumps(d.applied_constraints)
                        if d.applied_constraints
                        else None,
                        d.remediation_notes,
                        d.agent_that_flagged,
                        d.run_id,
                    ),
                )
            conn.commit()
        self._skip_cache = None
        logger.info("Bulk-saved %d decisions", len(decisions))

    # ------------------------------------------------------------------
    # Write API — constraint rules
    # ------------------------------------------------------------------

    def save_constraint_rule(self, rule: ConstraintRule) -> None:
        """Persist (or update) a constraint rule."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO constraint_rules
                    (rule_id, description, standard_remediation, llm_action,
                     reasoning, example_allowed, example_prohibited,
                     applies_to_patterns, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.rule_id,
                    rule.description,
                    rule.standard_remediation,
                    rule.llm_action,
                    rule.reasoning,
                    rule.example_allowed,
                    rule.example_prohibited,
                    json.dumps(rule.applies_to_patterns)
                    if rule.applies_to_patterns
                    else None,
                    rule.source_file,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Write API — run metadata
    # ------------------------------------------------------------------

    def save_run_metadata(
        self, run_id: str, config_snapshot: Optional[Dict[str, Any]] = None
    ) -> None:
        """Record metadata for an analysis run (audit trail)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_metadata
                    (run_id, timestamp, config_snapshot)
                VALUES (?, ?, ?)
                """,
                (
                    run_id,
                    datetime.now().isoformat(),
                    json.dumps(config_snapshot) if config_snapshot else None,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read API — decisions
    # ------------------------------------------------------------------

    def get_all_decisions(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[FeedbackDecision]:
        """Return all decisions, optionally filtered by field values."""
        query = "SELECT * FROM feedback_decisions WHERE 1=1"
        params: List[Any] = []

        if filters:
            if "issue_type" in filters:
                query += " AND issue_type = ?"
                params.append(filters["issue_type"])
            if "file_path" in filters:
                query += " AND file_path LIKE ?"
                params.append(f"%{filters['file_path']}%")
            if "human_action" in filters:
                query += " AND human_action = ?"
                params.append(filters["human_action"])
            if "severity" in filters:
                query += " AND severity = ?"
                params.append(filters["severity"])
            if "source" in filters:
                query += " AND source = ?"
                params.append(filters["source"])

        query += " ORDER BY timestamp DESC"

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_decision(r) for r in rows]

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
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM feedback_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return self._row_to_decision(row) if row else None

    # ------------------------------------------------------------------
    # Read API — constraint rules
    # ------------------------------------------------------------------

    def get_all_constraint_rules(self) -> List[ConstraintRule]:
        """Return every cached constraint rule."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM constraint_rules").fetchall()
        return [self._row_to_constraint_rule(r) for r in rows]

    def get_constraint_rule(self, rule_id: str) -> Optional[ConstraintRule]:
        """Retrieve a specific constraint rule by ID."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM constraint_rules WHERE rule_id = ?", (rule_id,)
            ).fetchone()
        return self._row_to_constraint_rule(row) if row else None

    # ------------------------------------------------------------------
    # Fast skip check
    # ------------------------------------------------------------------

    def get_skip_set(self) -> Set[Tuple[str, str]]:
        """Return a cached set of ``(issue_type, file_path)`` pairs marked SKIP.

        The cache is invalidated whenever new decisions are saved.
        """
        if self._skip_cache is not None:
            return self._skip_cache

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT issue_type, file_path FROM feedback_decisions "
                "WHERE human_action = 'SKIP'"
            ).fetchall()
        self._skip_cache = {(r[0], r[1]) for r in rows}
        return self._skip_cache

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        """Return aggregate statistics about the store."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM feedback_decisions"
            ).fetchone()[0]
            actions = conn.execute(
                "SELECT human_action, COUNT(*) FROM feedback_decisions "
                "GROUP BY human_action"
            ).fetchall()
            rules_count = conn.execute(
                "SELECT COUNT(*) FROM constraint_rules"
            ).fetchone()[0]

        return {
            "total_decisions": total,
            "total_constraints": rules_count,
            "actions_breakdown": {a: c for a, c in actions},
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Delete all data.  Use only for testing."""
        with self._connect() as conn:
            conn.execute("DELETE FROM feedback_decisions")
            conn.execute("DELETE FROM constraint_rules")
            conn.execute("DELETE FROM run_metadata")
            conn.commit()
        self._skip_cache = None
        logger.warning("Cleared all HITL store data")

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> FeedbackDecision:
        constraints_raw = row["applied_constraints"]
        return FeedbackDecision(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            source=row["source"],
            file_path=row["file_path"],
            line_number=row["line_number"],
            code_snippet=row["code_snippet"],
            issue_type=row["issue_type"] or "",
            severity=row["severity"] or "medium",
            human_action=row["human_action"],
            human_feedback_text=row["human_feedback_text"],
            applied_constraints=json.loads(constraints_raw)
            if constraints_raw
            else None,
            remediation_notes=row["remediation_notes"],
            agent_that_flagged=row["agent_that_flagged"],
            run_id=row["run_id"],
        )

    @staticmethod
    def _row_to_constraint_rule(row: sqlite3.Row) -> ConstraintRule:
        patterns_raw = row["applies_to_patterns"]
        return ConstraintRule(
            rule_id=row["rule_id"],
            description=row["description"] or "",
            standard_remediation=row["standard_remediation"] or "",
            llm_action=row["llm_action"] or "",
            reasoning=row["reasoning"],
            example_allowed=row["example_allowed"],
            example_prohibited=row["example_prohibited"],
            applies_to_patterns=json.loads(patterns_raw)
            if patterns_raw
            else None,
            source_file=row["source_file"],
        )
