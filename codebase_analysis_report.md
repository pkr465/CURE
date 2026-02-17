# QGenie CURE — Comprehensive Codebase Analysis

## Executive Summary

**CURE (Codebase Update & Refactor Engine)** is a sophisticated multi-agent pipeline for analyzing C/C++ codebases, producing health scores, identifying security vulnerabilities, and applying LLM-guided code fixes. It was built at Qualcomm and integrates with internal tooling (QGenie SDK) while supporting multiple external LLM providers (Anthropic Claude, Google Vertex AI, Azure OpenAI).

| Metric | Value |
|--------|-------|
| Total Python files | 86 |
| Total Python LOC | ~32,300 |
| Sample C codebase | `ieee80211_cfg80211.c` (38,750 lines) |
| Major subsystems | 7 (Agents, Analyzers, Adapters, DB, Dependency Builder, HITL, UI) |
| LLM providers supported | 4 (Anthropic, QGenie, Vertex AI, Azure OpenAI) |
| Orchestration framework | LangGraph |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│  CLI Entry Point (main.py — 1,346 lines)             │
│  Three modes: Standard Workflow │ LLM-Exclusive │ Patch│
└────────────────────┬─────────────────────────────────┘
                     │
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
┌────────┐   ┌────────────┐   ┌──────────┐
│LangGraph│   │CodebaseLLM │   │Patch     │
│Workflow │   │Agent (935L)│   │Agent     │
│(4 nodes)│   └──────┬─────┘   │(971L)   │
└───┬────┘          │         └────┬─────┘
    │               │              │
    ▼               ▼              ▼
┌─────────────────────────────────────────────┐
│  Core Analysis Engine                       │
│  ├─ StaticAnalyzerAgent (1,208L)            │
│  │   └─ 7-phase pipeline                    │
│  ├─ 9 Regex Analyzers + 6 Deep Adapters     │
│  ├─ MetricsCalculator (orchestrator)        │
│  └─ FileProcessor (discovery + caching)     │
└──────────────────┬──────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌───────────┐
│HITL    │  │Dependency│  │Database   │
│Feedback│  │Builder   │  │(PGVector) │
│Pipeline│  │(CCLS/LSP)│  │           │
└────────┘  └──────────┘  └───────────┘
                   │
                   ▼
          ┌────────────────┐
          │  UI (Streamlit) │
          │  + Fixer Agent  │
          │  (1,028L)       │
          └────────────────┘
```

---

## Subsystem Analysis

### 1. Entry Point & Orchestration (`main.py` — 1,346 lines)

The main entry point supports three operational modes:

**Standard Workflow (LangGraph)** — four sequential nodes:
1. `postgres_db_setup_agent` — idempotent PostgreSQL/pgvector initialization
2. `codebase_analysis_agent` — runs StaticAnalyzerAgent (7 phases)
3. `flatten_and_ndjson_agent` — JSON flattening → NDJSON for embeddings
4. `vector_db_ingestion_agent` — PGVector ingestion

**LLM-Exclusive Mode** (`--llm-exclusive`) — bypasses LangGraph entirely, runs `CodebaseLLMAgent` for per-file semantic analysis producing `detailed_code_review.xlsx`. When combined with `--enable-adapters`, deep adapter results merge into the same Excel as `static_*` tabs.

**Patch Analysis Mode** (`--patch-file`) — runs `CodebasePatchAgent` to diff-analyze patches and identify newly introduced issues.

**Strengths:**
- Graceful degradation via try/except imports with availability flags for every component
- Memory monitoring with `psutil` + forced GC after each phase
- Signal handling for clean shutdown (SIGINT/SIGTERM)
- Comprehensive CLI with 40+ flags and GlobalConfig YAML fallback

**Concerns:**
- ~240 lines of legacy argument parsing (pandoc, wmf2svg, toc) that appears unused
- Codebase path resolution logic duplicated in 3 places (main, run_workflow, codebase_analysis_agent)
- No parallelization despite independent file analysis potential

---

### 2. Agents Subsystem (~4,450 lines across 5 agents)

#### StaticAnalyzerAgent (1,208 lines)
The core workhorse implementing a 7-phase pipeline: file discovery → batch analysis → dependency graphs → health metrics → LLM enrichment → report generation → visualization. Coordinates 9 regex-based analyzers and optional deep static adapters.

#### CodebaseLLMAgent (935 lines)
Per-file semantic code review using LLM with smart brace-aware chunking, dependency context from CCLS, and constraint injection from markdown files. Produces `detailed_code_review.xlsx` with per-issue line-accurate findings.

#### CodebaseFixerAgent (1,028 lines)
Closes the loop between analysis and remediation. Reads JSONL fix directives, applies LLM-generated fixes with atomic writes, backup creation, integrity validation (>80% content preservation), and produces `final_execution_audit.xlsx`.

#### CodebasePatchAgent (971 lines)
Analyzes unified diff patches by running CodebaseLLMAgent on both original and patched versions, then fingerprint-based deduplication identifies truly new issues introduced by the patch.

#### CodebaseAnalysisChat (317 lines)
Interactive multi-turn conversational agent with intent extraction and vector database querying. Appears incomplete (file ends mid-implementation).

**Cross-cutting agent concerns:**
- Smart code chunking (brace-aware state machine tokenizer) is duplicated across 3 agents with similar but not identical logic
- Constraint injection from markdown files is a powerful feature but regex-based parsing is fragile
- No explicit LLM call timeouts beyond what LLMTools provides
- `sys.exit(1)` calls in agent code can abruptly terminate the pipeline

---

### 3. Analyzers (9 regex-based, ~2,900 lines total)

All inherit from `RuntimeAnalyzerBase` (64 lines) which provides template method pattern with brace-balance heuristic for function block extraction.

| Analyzer | Lines | Focus |
|----------|-------|-------|
| ComplexityAnalyzer | 732 | Cyclomatic/cognitive complexity, nesting, function length |
| SecurityAnalyzer | 908 | 50+ ScanBan-aligned rules, CWE mappings, severity scoring |
| MemoryCorruptionAnalyzer | 129 | UAF, double-free, realloc leak, format strings |
| NullPointerAnalyzer | ~130 | Null dereference detection |
| PotentialDeadlockAnalyzer | ~130 | Lock ordering, mutex-in-ISR detection |
| QualityAnalyzer | ~200 | Code quality heuristics |
| MaintainabilityAnalyzer | ~200 | Maintainability index calculation |
| DocumentationAnalyzer | ~200 | Comment ratio, documentation coverage |
| TestCoverageAnalyzer | ~200 | Test file detection, coverage estimation |

**Strengths:** Lightweight, zero external dependencies, fast execution. Severity-weighted scoring with configurable thresholds.

**Concerns:** Regex-based analysis has inherent false positive/negative rates. Comment stripping may miss preprocessor-based patterns. Thresholds are hardcoded (not configurable via YAML). No dataflow analysis for memory corruption or null pointer detection.

---

### 4. Deep Static Adapters (6 adapters)

All inherit from `BaseStaticAdapter` (107 lines) which enforces a standardized return format (score, grade, metrics, issues, details, tool_available).

| Adapter | Backend | Capability |
|---------|---------|------------|
| ASTComplexityAdapter | Lizard | Real cyclomatic complexity, nesting, parameters |
| SecurityAdapter | Flawfinder | CWE-mapped vulnerability scanning |
| DeadCodeAdapter | CCLS/libclang | BFS reachability from entry points |
| CallGraphAdapter | CCLS/libclang | Fan-in/fan-out, cycle detection |
| FunctionMetricsAdapter | CCLS/libclang | Body lines, parameters, templates |
| ExcelReportAdapter | ExcelWriter | Generates `static_*` Excel tabs |

Every adapter degrades gracefully when its backend tool is unavailable — a strong design choice for portability.

---

### 5. Database Subsystem (~1,900 lines)

**Pipeline:** `healthreport.json` → `JsonFlattener` (642L, produces 14 record types) → `NDJSONWriter` (205L) → `NDJSONProcessor` (449L, stable UUIDs + metadata) → `VectorDbPipeline` (245L, MD5 caching) → `PostgresVectorStore` (279L, pgvector + deduplication)

**Strengths:** Deterministic UUID generation for reproducible runs. MD5-based change detection to skip re-embedding. Deduplication via metadata UUID queries.

**Concerns:** Hardcoded vector dimension (1024) in schema. No batch optimization for bulk inserts. UUID deduplication via JSONB metadata query could be slow at scale. VectorDB wrapper advertises backend abstraction but only implements PostgreSQL.

---

### 6. Dependency Builder (~2,100 lines)

A full LSP client implementation for CCLS with connection pooling, caching, and health monitoring.

| Component | Lines | Purpose |
|-----------|-------|---------|
| DependencyService | 212 | High-level orchestrator with validation |
| DependencyFetcher | 456 | Request dispatch + file-aware cache invalidation |
| CCLSConnectionPool | 351 | Thread-safe process pool with eviction |
| Config | 183 | Typed configuration with env var overrides |
| Models | 347 | Request/response dataclasses with validation |
| Metrics | 294 | Thread-safe timing and cache hit/miss tracking |
| Exceptions | 223 | Typed exception hierarchy |

**Strengths:** Production-grade connection pooling. Content-hash-based cache invalidation. Comprehensive typed exception hierarchy. Thread-safe metrics collection (singleton pattern).

**Concerns:** Cache keys use basename only (collision risk across directories). No cache eviction policy (unbounded growth). Thread lock held for entire acquire operation (potential bottleneck). Lazy initialization has thread-safety concerns.

---

### 7. HITL (Human-in-the-Loop) Subsystem (~1,500 lines)

A feedback loop that persists human review decisions and constraint rules, then uses RAG-style retrieval to augment future analysis.

**Flow:** Excel feedback → `ExcelFeedbackParser` → `FeedbackStore` (SQLite) ← `ConstraintParser` (markdown) → `RAGRetriever` → `HITLContext` (unified API) → prompt augmentation

**Feedback actions:** FIX, SKIP, FIX_WITH_CONSTRAINTS, NEEDS_REVIEW
**Constraint sources:** Common (`common_constraints.md`) + file-specific (`<filename>_constraints.md`)

**Strengths:** Elegant facade pattern in `HITLContext`. O(1) skip-set checking with caching. Pluggable retrieval (upgradeable to semantic matching). Auto-persistence of agent decisions for learning loop.

**Concerns:** RAG retrieval is keyword/metadata-based (no ML/embeddings). Skip cache invalidated on any write (overzealous). Markdown constraint parsing is regex-based (brittle). No prompt length validation (could exceed context window).

---

### 8. Utilities (~2,100 lines)

**LLMTools (826L):** Multi-provider abstraction supporting QGenie, Anthropic, Vertex AI, Azure. Includes intent extraction, JSON parsing from LLM responses, token budget truncation (~4 chars/token heuristic), and semantic document retrieval.

**GlobalConfig (748L):** YAML-based hierarchical configuration with `${ENV_VAR}` interpolation, flat-key backward compatibility (120+ mappings), typed accessors, and path resolution. Includes a basic YAML parser fallback when PyYAML is unavailable.

**ExcelWriter (517L):** Professional Excel generation with openpyxl. Supports conditional formatting, auto-filters, freeze panes, alternating row colors, and summary rows.

---

### 9. UI (Streamlit Dashboard)

Interactive web dashboard for codebase analysis chat, metric visualization, and feedback collection. Uses `@st.cache_resource` for orchestrator caching. Integrates with the conversational chat agent and health report data.

---

## Key Strengths

1. **Graceful degradation everywhere** — every external tool, library, and subsystem is wrapped in try/except with availability flags. The system runs in degraded mode rather than failing.

2. **Constraint injection system** — markdown-based rules engine lets domain experts inject knowledge without code changes. Separate identification vs. resolution rules target different agents.

3. **HITL feedback loop** — persistent learning from human review decisions. Subsequent runs benefit from accumulated knowledge.

4. **Multi-provider LLM support** — `provider::model` format makes switching between Anthropic, QGenie, Vertex AI, and Azure a single config change.

5. **Atomic file operations** — the fixer agent uses temp-file-then-move writes with automatic backups, preventing corruption.

6. **Comprehensive audit trails** — every fix decision, skip, and failure is recorded in Excel audit reports.

7. **Memory-conscious design** — batch processing with psutil monitoring, forced GC, and configurable memory limits.

---

## Key Concerns & Recommendations

### Architecture

- **Code duplication:** Smart code chunking (brace-aware tokenizer) is implemented in 3 agents with minor variations. Extract to a shared `CodeChunker` utility.
- **Path resolution duplication:** Codebase path resolution logic appears in 3+ places. Centralize into GlobalConfig.
- **Legacy arguments:** ~240 lines of unused pandoc/wmf2svg CLI arguments. Remove or gate behind a `--legacy` flag.
- **Chat agent incomplete:** `codebase_analysis_chat_agent.py` ends mid-implementation (317 lines). Either complete or remove.

### Reliability

- **No LLM timeout enforcement:** Beyond what LLMTools provides, there's no explicit timeout on LLM calls. A hung provider could block the pipeline indefinitely.
- **`sys.exit()` in agents:** Several agents call `sys.exit(1)` on errors, which bypasses cleanup and audit logging. Use exceptions instead.
- **Regex-based analysis limitations:** The 9 analyzers are regex-only with no dataflow analysis. Consider supplementing with tree-sitter or clang AST for critical checks.
- **Cache unbounded growth:** The dependency builder cache has no eviction policy. Add LRU or TTL-based eviction.

### Scalability

- **No parallelization:** File analysis is sequential despite files being independent. Batch analysis could benefit from `concurrent.futures.ProcessPoolExecutor`.
- **Vector DB batch optimization:** Documents are inserted individually. Bulk insert with prepared statements would significantly improve ingestion speed.
- **FLAT_KEY_MAP growth:** The 120+ entry mapping in GlobalConfig is becoming unwieldy. Consider auto-generating from YAML schema.

### Security

- **Hardcoded credentials in config:** `global_config.yaml` contains a database password (`postgres`) in the connection string. These should always be `${ENV_VAR}` references.
- **Token counting heuristic:** The ~4 chars/token approximation in LLMTools may cause prompt truncation issues with different models. Use tiktoken or provider-specific tokenizers.

### Testing

- **No test suite detected.** The only test file found is `dependency_builder/test_dependency_services.py` and `agents/test_env.py`. Critical paths (analyzers, adapters, HITL, fixer) lack automated tests.
- **Threshold configurability:** Analysis thresholds (complexity >25, integrity >80%, etc.) are hardcoded. Making these configurable via YAML would enable tuning without code changes.

---

## File Inventory (by subsystem)

| Subsystem | Files | ~LOC | Key Entry Point |
|-----------|-------|------|-----------------|
| Root | 4 | 2,850 | `main.py`, `fixer_workflow.py` |
| Agents | 5 | 4,460 | `codebase_static_agent.py` |
| Analyzers | 11 | 2,900 | `base_runtime_analyzer.py` |
| Adapters | 7 | 1,000 | `base_adapter.py` |
| Core | 2 | 850 | `metrics_calculator.py` |
| Parsers | 3 | 600 | `healthreport_generator.py` |
| DB | 7 | 1,900 | `vectordb_pipeline.py` |
| Dependency Builder | 11 | 2,100 | `dependency_service.py` |
| HITL | 8 | 1,500 | `hitl_context.py` |
| Utils | 6 | 2,100 | `llm_tools.py` |
| UI | 4 | 500 | `streamlit_app.py` |
| Prompts | 3 | 400 | `prompts.py` |
| **Total** | **~86** | **~32,300** | |

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Orchestration | LangGraph (StateGraph) |
| LLM Providers | Anthropic Claude, QGenie, Google Vertex AI, Azure OpenAI |
| Static Analysis | Lizard, Flawfinder, CCLS/libclang |
| Vector Database | PostgreSQL + pgvector |
| Embeddings | QGenie Embeddings |
| Excel Reports | openpyxl, xlsxwriter |
| UI Dashboard | Streamlit |
| Configuration | YAML (GlobalConfig) + .env (EnvConfig) |
| Logging | Python logging + Rich console |
| Memory Monitoring | psutil |

---

*Analysis generated on February 17, 2026*
