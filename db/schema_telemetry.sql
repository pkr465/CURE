-- CURE — Codebase Update & Refactor Engine
-- PostgreSQL Schema for Telemetry & HITL Tables
-- Run with:
--   psql -U postgres -d codebase_analytics_db -a -e -f db/schema_telemetry.sql

------------------------------------------------------------
-- 1. Telemetry: Analysis/Fixer/Patch run summaries
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_runs (
    run_id              TEXT        PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    mode                TEXT        NOT NULL,  -- 'analysis' | 'fixer' | 'patch'
    status              TEXT        NOT NULL DEFAULT 'started',  -- 'started' | 'completed' | 'failed'

    -- Input context
    codebase_path       TEXT,
    files_analyzed      INTEGER     DEFAULT 0,
    total_chunks        INTEGER     DEFAULT 0,

    -- Issue counts
    issues_total        INTEGER     DEFAULT 0,
    issues_critical     INTEGER     DEFAULT 0,
    issues_high         INTEGER     DEFAULT 0,
    issues_medium       INTEGER     DEFAULT 0,
    issues_low          INTEGER     DEFAULT 0,

    -- Fixer outcomes
    issues_fixed        INTEGER     DEFAULT 0,
    issues_skipped      INTEGER     DEFAULT 0,
    issues_failed       INTEGER     DEFAULT 0,

    -- LLM usage
    llm_provider        TEXT,
    llm_model           TEXT,
    total_llm_calls     INTEGER     DEFAULT 0,
    total_prompt_tokens  INTEGER    DEFAULT 0,
    total_completion_tokens INTEGER DEFAULT 0,
    total_llm_latency_ms INTEGER   DEFAULT 0,

    -- Config flags
    use_ccls            BOOLEAN     DEFAULT FALSE,
    use_hitl            BOOLEAN     DEFAULT FALSE,
    constraints_used    TEXT,       -- comma-separated constraint filenames

    -- Duration
    duration_seconds    REAL,

    -- Free-form metadata
    metadata            JSONB
);

------------------------------------------------------------
-- 2. Telemetry: Granular events within a run
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id            BIGSERIAL   PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES telemetry_runs(run_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    event_type          TEXT        NOT NULL,
    -- Event types:
    --   'issue_found', 'issue_fixed', 'issue_skipped', 'issue_failed',
    --   'llm_call', 'export_action', 'constraint_applied',
    --   'hitl_decision', 'phase_change', 'error'

    -- Issue context (nullable — only for issue events)
    file_path           TEXT,
    line_number         INTEGER,
    issue_type          TEXT,
    severity            TEXT,

    -- LLM call details (nullable — only for llm_call events)
    llm_provider        TEXT,
    llm_model           TEXT,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    latency_ms          INTEGER,

    -- Generic payload
    detail              JSONB
);

------------------------------------------------------------
-- 3. HITL: Feedback decisions (migrated from SQLite)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hitl_feedback_decisions (
    id                  TEXT        PRIMARY KEY,
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
);

------------------------------------------------------------
-- 4. HITL: Constraint rules (migrated from SQLite)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hitl_constraint_rules (
    rule_id               TEXT  PRIMARY KEY,
    description           TEXT,
    standard_remediation  TEXT,
    llm_action            TEXT,
    reasoning             TEXT,
    example_allowed       TEXT,
    example_prohibited    TEXT,
    applies_to_patterns   JSONB,
    source_file           TEXT
);

------------------------------------------------------------
-- 5. HITL: Run metadata (migrated from SQLite)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hitl_run_metadata (
    run_id           TEXT        PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    config_snapshot  JSONB
);

------------------------------------------------------------
-- 6. Indexes for performance
------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_telemetry_runs_mode       ON telemetry_runs(mode);
CREATE INDEX IF NOT EXISTS idx_telemetry_runs_created     ON telemetry_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_run       ON telemetry_events(run_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_type      ON telemetry_events(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_created   ON telemetry_events(created_at);

CREATE INDEX IF NOT EXISTS idx_hitl_fd_issue_type         ON hitl_feedback_decisions(issue_type);
CREATE INDEX IF NOT EXISTS idx_hitl_fd_file_path          ON hitl_feedback_decisions(file_path);
CREATE INDEX IF NOT EXISTS idx_hitl_fd_human_action       ON hitl_feedback_decisions(human_action);
CREATE INDEX IF NOT EXISTS idx_hitl_fd_run_id             ON hitl_feedback_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_hitl_cr_rule_id            ON hitl_constraint_rules(rule_id);
