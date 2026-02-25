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
-- 6. Telemetry: Per-finding detail (granular issue tracking)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_findings (
    finding_id          BIGSERIAL   PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES telemetry_runs(run_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Location
    file_path           TEXT,
    line_start          INTEGER,
    line_end            INTEGER,

    -- Issue details
    title               TEXT,
    category            TEXT,       -- 'Security' | 'Memory' | 'Error Handling' | 'Performance' | 'Concurrency' | 'Logic'
    severity            TEXT,       -- 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
    confidence          TEXT,       -- 'CERTAIN' | 'PROBABLE' | 'POSSIBLE'
    description         TEXT,
    suggestion          TEXT,
    code_snippet        TEXT,
    fixed_code          TEXT,

    -- Feedback tracking
    is_false_positive   BOOLEAN     DEFAULT FALSE,
    user_feedback       TEXT,

    -- Free-form metadata
    metadata            JSONB
);

------------------------------------------------------------
-- 7. Telemetry: Per-invocation LLM call tracking with cost
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_llm_calls (
    call_id             BIGSERIAL   PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES telemetry_runs(run_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Model info
    provider            TEXT,
    model               TEXT,
    purpose             TEXT,       -- 'analysis' | 'fix' | 'patch_review' | 'chat' | 'intent'

    -- Context
    file_path           TEXT,
    chunk_index         INTEGER,

    -- Token usage
    prompt_tokens       INTEGER     DEFAULT 0,
    completion_tokens   INTEGER     DEFAULT 0,
    total_tokens        INTEGER     DEFAULT 0,

    -- Performance
    latency_ms          INTEGER     DEFAULT 0,
    estimated_cost_usd  NUMERIC(10,6) DEFAULT 0,

    -- Status
    status              TEXT        DEFAULT 'success',  -- 'success' | 'error' | 'timeout'
    error_message       TEXT,

    -- Free-form metadata
    metadata            JSONB
);

------------------------------------------------------------
-- 8. Telemetry: Constraint hits (which rules fired)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_constraint_hits (
    hit_id              BIGSERIAL   PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES telemetry_runs(run_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    constraint_source   TEXT,       -- file path of the constraint
    constraint_rule     TEXT,       -- rule description or ID
    file_path           TEXT,       -- file where constraint applied
    issue_type          TEXT,
    action              TEXT,       -- 'suppressed' | 'modified' | 'ignored' | 'hitl_suppressed'

    -- Free-form metadata
    metadata            JSONB
);

------------------------------------------------------------
-- 9. Telemetry: Per-adapter static analysis results
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_static_analysis (
    result_id           BIGSERIAL   PRIMARY KEY,
    run_id              TEXT        NOT NULL REFERENCES telemetry_runs(run_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    adapter_name        TEXT,       -- 'ast_complexity' | 'security' | 'dead_code' | 'call_graph' | 'function_metrics'
    file_path           TEXT,
    findings_count      INTEGER     DEFAULT 0,

    -- Adapter-specific metrics
    metrics             JSONB,

    -- Free-form metadata
    metadata            JSONB
);

------------------------------------------------------------
-- 10. Telemetry: Materialized usage reports (daily/weekly)
------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_usage_reports (
    report_id           BIGSERIAL   PRIMARY KEY,
    report_date         DATE        NOT NULL,
    report_type         TEXT        NOT NULL,  -- 'daily' | 'weekly'

    -- Aggregates
    total_runs          INTEGER     DEFAULT 0,
    total_files         INTEGER     DEFAULT 0,
    total_findings      INTEGER     DEFAULT 0,
    total_fixes         INTEGER     DEFAULT 0,
    total_tokens        INTEGER     DEFAULT 0,
    estimated_cost_usd  NUMERIC(10,4) DEFAULT 0,

    -- Top-N breakdowns
    top_issue_types     JSONB,
    top_files           JSONB,

    -- Free-form metadata
    metadata            JSONB,

    UNIQUE(report_date, report_type)
);

------------------------------------------------------------
-- 11. Indexes for performance
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

-- Telemetry findings indexes
CREATE INDEX IF NOT EXISTS idx_findings_run               ON telemetry_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_run_created        ON telemetry_findings(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_findings_severity           ON telemetry_findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_category           ON telemetry_findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_file               ON telemetry_findings(file_path);
CREATE INDEX IF NOT EXISTS idx_findings_false_positive      ON telemetry_findings(is_false_positive) WHERE is_false_positive = TRUE;

-- Telemetry LLM calls indexes
CREATE INDEX IF NOT EXISTS idx_llm_calls_run              ON telemetry_llm_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run_created       ON telemetry_llm_calls(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_provider_model    ON telemetry_llm_calls(provider, model);
CREATE INDEX IF NOT EXISTS idx_llm_calls_purpose           ON telemetry_llm_calls(purpose);

-- Telemetry constraint hits indexes
CREATE INDEX IF NOT EXISTS idx_constraint_hits_run         ON telemetry_constraint_hits(run_id);
CREATE INDEX IF NOT EXISTS idx_constraint_hits_action       ON telemetry_constraint_hits(action);
CREATE INDEX IF NOT EXISTS idx_constraint_hits_rule         ON telemetry_constraint_hits(constraint_rule);

-- Telemetry static analysis indexes
CREATE INDEX IF NOT EXISTS idx_static_analysis_run         ON telemetry_static_analysis(run_id);
CREATE INDEX IF NOT EXISTS idx_static_analysis_adapter      ON telemetry_static_analysis(adapter_name);

-- Telemetry usage reports indexes
CREATE INDEX IF NOT EXISTS idx_usage_reports_date          ON telemetry_usage_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_usage_reports_type           ON telemetry_usage_reports(report_type);
