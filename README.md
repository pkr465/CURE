# QGenie — C/C++ Codebase Update & Refactor Engine

`main.py` is the main entry point for a **multi-stage C/C++ codebase health analysis pipeline**. It is designed to analyze C/C++ codebases and produce rich health scores, structural metadata, and embeddings suitable for RAG (Retrieval-Augmented Generation) applications.

## Key Capabilities

1. **Static Analysis**: Uses a unified `StaticAnalyzerAgent` (7-phase pipeline) with 9 regex-based analyzers for fast health scoring.
2. **Deep Static Analysis Adapters**: Optional `--enable-adapters` mode powered by CCLS/libclang, Lizard, and Flawfinder for AST-accurate metrics.
3. **LLM-Powered Code Review**: `CodebaseLLMAgent` performs per-file semantic analysis and produces `detailed_code_review.xlsx`.
4. **Health Reporting**: Produces a canonical `healthreport.json` with metrics and summaries; optional HTML rendering.
5. **Data Flattening**: Converts reports to JSON and NDJSON formats for embedding.
6. **Vector DB Ingestion**: Ingests data into a **PostgreSQL** vector database with pgvector.
7. **Agentic Code Repair**: Human-in-the-loop `CodebaseFixerAgent` applies LLM-suggested fixes guided by reviewer feedback.
8. **Multi-Provider LLM**: Supports Anthropic Claude, QGenie, Google Vertex AI, and Azure OpenAI via `provider::model` format.
9. **Visualization**: Generates an HTML health report and provides a Streamlit UI dashboard.
10. **Telemetry & Analytics**: Silent PostgreSQL-backed telemetry tracks issues found/fixed, LLM usage, run durations, and fix success rates with a built-in dashboard.
11. **HITL Feedback Store**: PostgreSQL-backed persistent store for human feedback decisions and constraint rules, enabling agents to learn from accumulated human review history.

---

## Architecture & Workflow

### Standard Workflow (LangGraph)

The workflow is orchestrated using **LangGraph** and consists of four main agents:

1. **PostgreSQL Setup** (`postgres_db_setup_agent`): Sets up the schema and tables for vector storage.
2. **Codebase Analysis** (`codebase_analysis_agent`): Runs `StaticAnalyzerAgent` (7-phase pipeline with optional LLM enrichment and deep adapters). Generates `healthreport.json`.
3. **Flatten & NDJSON** (`flatten_and_ndjson_agent`): Flattens the report (`JsonFlattener`) and converts it to NDJSON (`NDJSONProcessor`) for embedding processing.
4. **Vector DB Ingestion** (`vector_db_ingestion_agent`): Ingests the processed records into PostgreSQL via `VectorDbPipeline`.

```text
PostgreSQL Setup
    ↓
Codebase Analysis (StaticAnalyzerAgent — 9 analyzers + optional deep adapters)
    ↓
Flatten & NDJSON
    ↓
Vector DB Ingestion
```

### Exclusive LLM Mode (`--llm-exclusive`)

Bypasses the LangGraph workflow entirely. Runs `CodebaseLLMAgent` for per-file semantic analysis producing `detailed_code_review.xlsx`. When combined with `--enable-adapters`, deep adapter results (complexity, security, dead code, call graph, function metrics) are merged as `static_*` tabs in the same Excel file.

```text
[Optional] Deep Static Adapters (Lizard, Flawfinder, CCLS)
    ↓
CodebaseLLMAgent (per-file LLM analysis)
    ↓
detailed_code_review.xlsx (LLM tabs + static_ adapter tabs)
```

### Deep Static Analysis Adapters (`--enable-adapters`)

When enabled, the following adapters run using real tooling instead of regex heuristics:

| Adapter                  | Backend       | Capabilities                                                |
| :----------------------- | :------------ | :---------------------------------------------------------- |
| `ASTComplexityAdapter`   | Lizard        | Real cyclomatic complexity, nesting depth, parameter counts |
| `SecurityAdapter`        | Flawfinder    | CWE-mapped vulnerability scanning with severity levels      |
| `DeadCodeAdapter`        | CCLS/libclang | BFS reachability analysis from entry points                 |
| `CallGraphAdapter`       | CCLS/libclang | Fan-in/fan-out, cycle detection, max call depth             |
| `FunctionMetricsAdapter` | CCLS/libclang | Function body lines, parameters, templates, virtuals        |
| `ExcelReportAdapter`     | ExcelWriter   | Generates `static_*` prefixed tabs in Excel output          |

All adapters inherit from `BaseStaticAdapter` and degrade gracefully when their underlying tool is unavailable.

### Project Layout

```text
.
├── main.py                             # Entry point & LangGraph workflow
├── fixer_workflow.py                   # Human-in-the-loop repair workflow
├── global_config.yaml                  # Hierarchical YAML configuration
├── requirements.txt                    # Python dependencies
├── agents/
│   ├── codebase_static_agent.py        # Unified 7-phase static analyzer
│   ├── codebase_llm_agent.py           # LLM-exclusive per-file code reviewer
│   ├── codebase_fixer_agent.py         # Agentic code repair agent (source-aware, audit trail)
│   ├── codebase_patch_agent.py         # Patch analysis agent (diff-based issue detection)
│   ├── codebase_analysis_chat_agent.py # Interactive chat analysis agent
│   ├── adapters/                       # Deep static analysis adapters
│   │   ├── base_adapter.py             #   ABC base class
│   │   ├── ast_complexity_adapter.py   #   Lizard integration
│   │   ├── security_adapter.py         #   Flawfinder integration
│   │   ├── dead_code_adapter.py        #   CCLS dead code detection
│   │   ├── call_graph_adapter.py       #   CCLS call graph analysis
│   │   ├── function_metrics_adapter.py #   CCLS function metrics
│   │   └── excel_report_adapter.py     #   static_ Excel tab generator
│   ├── context/                        # Header context injection for LLM analysis
│   │   ├── __init__.py
│   │   └── header_context_builder.py   #   Include resolution, header parsing, context assembly
│   ├── analyzers/                      # 9 regex-based health analyzers
│   │   ├── base_runtime_analyzer.py    #   ABC base for all analyzers
│   │   ├── complexity_analyzer.py
│   │   ├── security_analyzer.py
│   │   ├── dependency_analyzer.py
│   │   ├── memory_corruption_analyzer.py
│   │   ├── null_pointer_analyzer.py
│   │   ├── potential_deadlock_analyzer.py
│   │   ├── quality_analyzer.py
│   │   ├── maintainability_analyzer.py
│   │   ├── documentation_analyzer.py
│   │   └── test_coverage_analyzer.py
│   ├── core/
│   │   ├── file_processor.py           # File discovery & caching
│   │   └── metrics_calculator.py       # Orchestrates analyzers + adapters
│   ├── prompts/
│   │   └── prompts.py                  # PromptTemplates for LLM agents
│   ├── parsers/
│   │   ├── excel_to_agent_parser.py    # Excel → JSONL directives parser
│   │   ├── healthreport_generator.py   # HTML health report renderer
│   │   └── healthreport_parser.py      # Legacy health report parser
│   ├── visualization/
│   │   └── graph_generator.py          # Dependency graph visualization
│   └── vector_db/
│       └── document_processor.py       # Vector document processing
├── db/
│   ├── postgres_db_setup.py            # PostgreSQL schema setup (vector + telemetry + HITL)
│   ├── postgres_api.py                 # PostgreSQL API helpers
│   ├── telemetry_service.py            # Silent telemetry collector (TelemetryService)
│   ├── schema_telemetry.sql            # SQL schema for telemetry & HITL tables
│   ├── json_flattner.py                # JSON → flat JSON converter
│   ├── ndjson_processor.py             # NDJSON processor for embeddings
│   ├── ndjson_writer.py                # NDJSON file writer
│   ├── vectordb_pipeline.py            # Vector DB ingestion pipeline
│   └── vectordb_wrapper.py             # Vector DB abstraction wrapper
├── dependency_builder/                 # CCLS / libclang integration
│   ├── ccls_code_navigator.py          # LSP-based code navigation
│   ├── ccls_ingestion.py               # CCLS indexing orchestrator
│   ├── ccls_dependency_builder.py      # Dependency graph builder
│   ├── dependency_service.py           # Dependency resolution service
│   ├── dependency_handler.py           # Dependency processing handler
│   ├── connection_pool.py              # LSP connection pooling
│   ├── config.py                       # DependencyBuilderConfig
│   ├── models.py                       # Data models
│   ├── metrics.py                      # Performance metrics
│   ├── lsp_notification_handlers.py    # CCLS LSP notification handlers
│   ├── exceptions.py                   # Custom exceptions
│   └── utils.py                        # Shared utilities
├── hitl/                               # Human-in-the-Loop RAG pipeline
│   ├── __init__.py                     # Module exports, HITL_AVAILABLE flag
│   ├── config.py                       # HITLConfig dataclass (PostgreSQL connection)
│   ├── schemas.py                      # Data models (FeedbackDecision, ConstraintRule)
│   ├── feedback_store.py               # PostgreSQL persistent store (migrated from SQLite)
│   ├── excel_feedback_parser.py        # Parse Excel human feedback
│   ├── constraint_parser.py            # Parse *_constraints.md files
│   ├── rag_retriever.py                # RAG query engine
│   ├── hitl_context.py                 # Unified agent interface (HITLContext)
│   └── prompts.py                      # RAG-augmented prompt templates
├── utils/
│   ├── common/
│   │   ├── llm_tools.py                # Multi-provider LLM abstraction
│   │   ├── excel_writer.py             # Excel report generator
│   │   ├── email_reporter.py           # SMTP email reporter
│   │   └── mmdtopdf.py                # Mermaid → PDF converter
│   ├── parsers/
│   │   ├── env_parser.py               # .env / environment config
│   │   └── global_config_parser.py     # YAML GlobalConfig parser
│   └── prompts/
│       └── prompts.py                  # Utility prompt helpers
├── prompts/
│   └── codebase_analysis_prompt.py     # LLM analysis prompt template
└── ui/
    ├── app.py                          # Main Streamlit app (7 workflow tabs)
    ├── streamlit_tools.py              # Custom Streamlit helpers (sidebar, themes)
    ├── background_workers.py           # Thread runners with telemetry instrumentation
    ├── feedback_helpers.py             # Excel feedback persistence helpers
    ├── qa_inspector.py                 # QA validation inspector
    └── launch_streamlit.py             # Streamlit launcher
```

---

## Installation & Setup

### 1. System Prerequisites

**Python 3.12+** is recommended.



**Install PostgreSQL and initialize the database** (required for vector DB pipeline):

**macOS / Linux (Option A — automated bootstrap):**
```bash
sudo ./bootstrap_db.sh
```
The script auto-detects the installed PostgreSQL version, installs pgvector (building from source if the Homebrew bottle doesn't match your PG version), creates the user, database, extension, and permissions.

**macOS (Option B — manual Homebrew setup):**
```bash
# Install PostgreSQL and start the service
brew install postgresql@16
brew services start postgresql@16

# Install pgvector (build from source if brew bottle doesn't cover your PG version)
brew install pgvector
# If CREATE EXTENSION fails later, build from source:
cd /tmp && git clone --branch v0.8.1 --depth 1 https://github.com/pgvector/pgvector.git
cd pgvector && PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config make && sudo make install

# Create the database, user, and extension
/opt/homebrew/opt/postgresql@16/bin/psql -d postgres -c "CREATE USER codebase_analytics_user WITH PASSWORD 'postgres';"
/opt/homebrew/opt/postgresql@16/bin/psql -d postgres -c "CREATE DATABASE codebase_analytics_db OWNER codebase_analytics_user;"
/opt/homebrew/opt/postgresql@16/bin/psql -d codebase_analytics_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Windows (PowerShell — run as Administrator):**
```powershell
.\bootstrap_db.ps1
```

All options install PostgreSQL with pgvector, create the application user and database, enable the vector extension, and grant all required permissions. Defaults match `global_config.yaml`. Override with environment variables if needed:

## Optional
```bash
# macOS / Linux
DB_USER=myuser DB_PASSWORD=mypass DB_NAME=mydb sudo -E ./bootstrap_db.sh

# Windows PowerShell
$env:DB_USER="myuser"; $env:DB_PASSWORD="mypass"; $env:DB_NAME="mydb"; .\bootstrap_db.ps1
```

### 2. Python Environment Setup

```bash
sudo su
```

```bash
# if there is already an .env
deactivate
rm -rf .venv

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate
python --version
```

### 3. Install Dependencies

```bash
pip install --upgrade pip

pip install qgenie-sdk[all] qgenie-sdk-tools -i https://devpi.qualcomm.com/qcom/dev/+simple --trusted-host devpi.qualcomm.com
pip install -r requirements.txt
```

**Install CCLS** (required for `dependency_builder` and deep adapters):

```bash
# macOS
brew install ccls

# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y ccls

# snap alternative
sudo snap install ccls --classic

# Windows (via Chocolatey)
choco install ccls
```

### install ccls and clang 
```bash
sudo apt install clang-14 libclang-14-dev llvm-14-dev
export CC=clang-14
export CXX=clang++-14

#verify
echo $CC
echo $CXX
```

### 4. Configuration

Copy and customize the global configuration:

```bash
cp global_config.yaml.example global_config.yaml
# OR edit global_config.yaml directly
```

Set your LLM provider in `global_config.yaml`:

```yaml
llm:
  model: anthropic::claude-sonnet-4-20250514   # Anthropic Claude
  # model: qgenie::qwen2.5-14b-1m              # QGenie
  # model: vertexai::gemini-2.5-pro             # Google Vertex AI
  # model: azure::gpt-4.1                       # Azure OpenAI
```

Set API keys via environment or `.env`:

```bash
cp env.example .env
# Edit .env:
#   ANTHROPIC_API_KEY=sk-...
#   QGENIE_API_KEY=...
#   POSTGRES_PASSWORD=...
```

## 🛡️ Constraints & Rules Engine

The CURE ecosystem uses a **Context-Aware Constraints System**. This allows you to inject domain-specific knowledge, hardware limitations, and coding standards into the LLM agents without modifying the source code.

Constraints are defined in Markdown files and are split into two specific categories:
1.  **Issue Identification Rules:** Tell the Analysis Agent what to **IGNORE** (False Positive filtering).
2.  **Issue Resolution Rules:** Tell the Fixer Agent **HOW TO FIX** the code (Performance/Style guidelines).

### 📂 1. Directory Structure

All constraint files must be placed in the **`agents/constraints/`** directory.

| Scope        | File Naming Convention      | Description                                                                                                             |
| :----------- | :-------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| **Global**   | `common_constraints.md`     | Rules applied to **ALL** files during analysis or fixing. Use this for project-wide standards.                          |
| **Specific** | `<filename>_constraints.md` | Rules applied **ONLY** to a specific source file.<br>_Example:_ `dp_main.c_constraints.md` applies only to `dp_main.c`. |

### 📝 2. File Format & Required Headers

The agents use **Regex Parsing** to extract rules. You **MUST** use the specific Markdown headers below for the system to recognize your instructions.

#### A. The Identification Section
*   **Target Agent:** `CodebaseLLMAgent`, `CodebasePatchAgent`
*   **Required Header:** `## Issue Identification Rules`
*   **Purpose:** To prevent the LLM from flagging known safe patterns (e.g., "Hardware registers are never NULL").

#### B. The Resolution Section
*   **Target Agent:** `CodebaseFixerAgent`
*   **Required Header:** `## Issue Resolution Rules`
*   **Purpose:** To guide the LLM on how to generate code (e.g., "Do not use Mutex in ISR", "Use `memcpy` instead of `memcpy_s`").

### 💡 3. Constraint File Template

Create a file (e.g., `agents/constraints/my_driver.c_constraints.md`) using this structure:

```markdown
# Constraints for my_driver.c

## 1. Issue Identification Rules (WHAT TO IGNORE)
*Use these rules to filter out False Positives during analysis.*

### A. Hardware Contexts
*   **Rule**: Variables `soc`, `pdev`, `hif_ctx` are initialized at boot. **IGNORE** missing NULL checks in data paths.
*   **Rule**: **IGNORE** array bounds checks if the index is `loop_counter % MAX_HW_QUEUES`.

---

## 2. Issue Resolution Rules (HOW TO FIX)
*Use these rules when generating code fixes.*

### A. Critical Performance
*   **Rule**: **DO NOT** introduce locking (mutex/spinlocks) in `isr_handler` functions.
*   **Rule**: **RETAIN** `memcpy` for performance. Do not replace with `memcpy_s`. Instead, add an explicit `if (len > size)` check before the call.

### B. Error Handling
*   **Rule**: In `void` functions, prefer `return;` over complex error handling. Do not log to serial console in hot paths.
```

### ⚙️ 4. Workflow Integration

| Agent            | Input Section          | Behavior                                                                                                        |
| :--------------- | :--------------------- | :-------------------------------------------------------------------------------------------------------------- |
| **LLM Analysis** | `Identification Rules` | Injects rules into the system prompt to filter out False Positives before they are reported.                    |
| **Patch Agent**  | `Identification Rules` | Uses these rules to analyze diffs, ensuring that new code is checked against project-specific ignore lists.     |
| **Fixer Agent**  | `Resolution Rules`     | Injects rules into the code generation prompt to ensure fixes comply with performance budgets and style guides. |
--------------------------------------------------------------------------------------------------------------------------------------------------------------

## Usage

### CLI Reference

| Flag                            | Description                                                               |
| :------------------------------ | :------------------------------------------------------------------------ |
| `--codebase-path PATH`          | Path to the C/C++ codebase to analyze                                     |
| `-d, --out-dir DIR`             | Output directory for generated files (default: `./out`)                   |
| `--config-file PATH`            | Path to `global_config.yaml` (default: auto-detected)                     |
| `--use-llm`                     | Enable LLM enrichment in StaticAnalyzerAgent (health report mode)         |
| `--llm-exclusive`               | Use CodebaseLLMAgent exclusively for Excel report (skips health pipeline) |
| `--enable-adapters`             | Run deep static analysis(Lizard, Flawfinder, CCLS) --llm-exclusive requ   |
| `--use-ccls`                    | Enable CCLS dependency services for CodebaseLLMAgent                      |
| `--file-to-fix FILE`            | Analyze a specific file (relative to codebase path)                       |
| `--llm-model MODEL`             | LLM model in `provider::model` format (overrides config)                  |
| `--llm-api-key KEY`             | API key for the LLM provider                                              |
| `--llm-max-tokens N`            | Max tokens per LLM request (default: 15000)                               |
| `--llm-temperature F`           | LLM temperature (default: 0.1)                                            |
| `--max-files N`                 | Max files to analyze (default: 2000)                                      |
| `--batch-size N`                | Files per analysis batch (default: 25)                                    |
| `--exclude-dirs D [D]`          | Directories to exclude (merged with `scanning.exclude_dirs` config)       |
| `--exclude-globs G [G]`         | Glob patterns to exclude (merged with `scanning.exclude_globs` config)    |
| `--generate-constraints`        | Auto-generate `codebase_constraints.md` from symbols and exit             |
| `--include-custom-constraints F [F]` | Additional custom constraint `.md` files to include in analysis      |
| `--enable-vector-db`            | Enable vector DB ingestion pipeline                                       |
| `--vector-chunk-size N`         | Chunk size for vector embeddings (default: 4000)                          |
| `--vector-overlap-size N`       | Overlap between chunks (default: 200)                                     |
| `--vector-include-code`         | Include source code in vector embeddings (default: on)                    |
| `--enable-chatbot-optimization` | Enable chatbot-optimized vector processing                                |
| `--generate-report`             | Generate HTML health report from healthreport.json                        |
| `--generate-visualizations`     | Generate dependency graph visualizations                                  |
| `--generate-pdfs`               | Generate PDF outputs from Mermaid diagrams                                |
| `--max-edges N`                 | Max edges in graph visualizations (default: 500)                          |
| `--health-report-path PATH`     | Override path for healthreport.json output                                |
| `--flat-json-path PATH`         | Override path for flattened JSON output                                   |
| `--ndjson-path PATH`            | Override path for NDJSON output                                           |
| `--force-reanalysis`            | Force re-analysis ignoring cached results                                 |
| `--memory-limit MB`             | Memory limit in MB (default: 3000)                                        |
| `--enable-memory-monitoring`    | Enable real-time memory monitoring (default: on)                          |
| `--patch-file PATH`             | Path to `.patch`/`.diff` file for patch analysis                          |
| `--patch-target PATH`           | Path to the original source file being patched                            |
| `--enable-hitl`                 | Enable Human-in-the-Loop RAG feedback system                              | `false`                  |
| `--hitl-feedback-excel`         | Path to `detailed_code_review.xlsx` with human feedback                   | `None`                   |
| `--hitl-constraints-dir`        | Directory to search for `*_constraints.md` files                          | `None`                   |
| `--hitl-store-path`             | Legacy (deprecated) — HITL now uses PostgreSQL                            | *(ignored)*              |
| `-v, --verbose`                 | Verbose logging                                                           |
| `-D, --debug`                   | Debug logging                                                             |
| `--quiet`                       | Suppress non-error output                                                 |

#### Fixer Workflow CLI (`fixer_workflow.py`)

| Flag                                  | Description                                                                                              |
| :------------------------------------ | :------------------------------------------------------------------------------------------------------- |
| `--excel-file PATH`                   | Path to the reviewed Excel file (default: `out/detailed_code_review.xlsx`)                               |
| `--codebase-path PATH`                | Root directory of the source code                                                                        |
| `--out-dir DIR`                       | Directory for backups/intermediate files                                                                 |
| `--fix-source {all,llm,static,patch}` | Process only issues from: all, llm (Analysis sheet), static (static_* sheets), or patch (patch_* sheets) |
| `--llm-model MODEL`                   | LLM model in `provider::model` format                                                                    |
| `--dry-run`                           | Simulate fixes without writing to disk                                                                   |

### Standard Analysis (Health Report Pipeline)

```bash
# Basic static analysis (fast, regex-based)
python main.py --codebase-path /path/to/cpp/project

# With LLM enrichment
python main.py --codebase-path /path/to/cpp/project --use-llm

# With deep static adapters (Lizard + Flawfinder + CCLS) - --llm-exclusive is required to generate xlsx.
python main.py --codebase-path /path/to/cpp/project --enable-adapters --llm-exclusive

# Full pipeline with vector DB
python main.py --codebase-path /path/to/cpp/project \
  --use-llm --enable-adapters --enable-vector-db --generate-report
```

### Exclusive LLM Analysis (Direct Excel Report)

This mode skips the LangGraph health pipeline and generates `detailed_code_review.xlsx` directly.

```bash
# LLM-only analysis
python main.py --llm-exclusive --codebase-path /path/to/cpp/project

# LLM + deep adapters (static_ tabs merged into same Excel)
python main.py --llm-exclusive --enable-adapters --codebase-path /path/to/cpp/project

# With CCLS dependency context
python main.py --llm-exclusive --enable-adapters --use-ccls \
  --codebase-path /path/to/cpp/project

# Targeted single-file analysis
python main.py --llm-exclusive --use-ccls \
  --file-to-fix "src/module/component.cpp" \
  --codebase-path /path/to/cpp/project
```

### LLM Provider Selection

```bash
# Anthropic Claude
python main.py --llm-exclusive --llm-model "anthropic::claude-sonnet-4-20250514" \
  --codebase-path /path/to/project

# Google Vertex AI
python main.py --llm-exclusive --llm-model "vertexai::gemini-2.5-pro" \
  --codebase-path /path/to/project

# Azure OpenAI
python main.py --llm-exclusive --llm-model "azure::gpt-4.1" \
  --codebase-path /path/to/project
```

### Streamlit Dashboard

```bash
python -m streamlit run ui/app.py --server.port 8502
```

Access at: `http://localhost:8502`

The UI provides seven workflow tabs: **Analyze**, **Pipeline**, **Review**, **Fix & QA**, **Audit**, **Constraints**, and **Telemetry**. The sidebar includes toggles for enabling HITL and Telemetry.

---

## Agentic Code Repair (Human-in-the-Loop)

The pipeline includes a **CodebaseFixerAgent** that closes the loop between analysis and remediation.

### Workflow

1. **Analyze**: Run `main.py` to generate `detailed_code_review.xlsx`.
2. **Human Review**: Open the Excel, review High/Critical issues, add feedback and constraints.
3. **Execute Fixes**: Run `fixer_workflow.py` to apply LLM-guided fixes.

### Fixer Commands

```bash
# Single-step (parse + fix)
python fixer_workflow.py --excel detailed_code_review.xlsx --codebase-path /path/to/project

# Parse Excel to JSONL only
python fixer_workflow.py --step parse --excel detailed_code_review.xlsx

# Run the fixer agent only
python fixer_workflow.py --step fix --codebase-path /path/to/project
```

### Feedback & Constraints Columns

**Feedback column** — controls the action:

| User Input                    | Effect                                                |
| :---------------------------- | :---------------------------------------------------- |
| *(Empty)*                     | **Approve.** Apply `Fixed_Code` as suggested.         |
| `Skip` / `Ignore` / `No Fix`  | **Reject.** File untouched (false positive).          |
| `Approved` / `LGTM`           | **Approve.** Explicit confirmation.                   |
| `Modify` / `Update` / `Retry` | **Custom fix.** Re-generate using Constraints column. |

**Constraints column** — provides technical guardrails for custom fixes, for example: "Use `std::array` instead of `std::vector`", "Follow C++98 only", "Wrap in `std::lock_guard`", etc.

### Fixer Features

- **Holistic Refactoring**: Fixes multiple issues in a single file simultaneously for consistency.
- **Smart Backups**: Creates a mirror in `out/shelved_backups` before modifying any file.
- **Safety Gates**: Checks for file size anomalies and LLM failures before overwriting.
- **Audit Reporting**: Produces `final_execution_audit.xlsx` with color-coded status (FIXED, SKIPPED, FAILED).

---

## Configuration Reference

### global_config.yaml

The `global_config.yaml` file provides hierarchical, typed configuration with `${ENV_VAR}` override support. Key sections:

| Section              | Purpose                                               |
| :------------------- | :---------------------------------------------------- |
| `paths`              | Input/output directories, prompt file paths           |
| `llm`                | Provider, model, API keys, token limits, temperature  |
| `embeddings`         | Vector embedding model selection                      |
| `database`           | PostgreSQL connection, PGVector collection settings   |
| `email`              | SMTP report delivery configuration                    |
| `scanning`           | File discovery exclusions — directory names and glob patterns to skip |
| `dependency_builder` | CCLS executable, timeouts, BFS depth, connection pool |
| `excel`              | Report styling (colors, column widths, freeze/filter) |
| `mermaid`            | Diagram rendering configuration                       |
| `hitl`               | HITL RAG pipeline — feedback store, constraint parsing |
| `context`            | Header context injection — include paths, depth, token budget |
| `telemetry`          | Silent usage telemetry (enable/disable)               |
| `logging`            | Log level, verbose/debug flags                        |

---

## Telemetry & Analytics

CURE includes a silent, fire-and-forget telemetry system that records framework usage patterns into the same PostgreSQL database (`codebase_analytics_db`). Telemetry is enabled by default and can be toggled in `global_config.yaml` or the Streamlit sidebar.

### What is tracked

- **Run summaries**: mode (analysis/fixer/patch), status, duration, file count, issue counts by severity
- **Fix outcomes**: issues fixed, skipped, and failed per fixer run
- **LLM usage**: provider, model, token counts (prompt + completion), latency per call
- **Granular events**: individual issue found/fixed/skipped events, phase transitions, export actions

### Configuration

```yaml
# global_config.yaml
telemetry:
  enable: true   # set to false to disable all telemetry
```

### Database tables

| Table                | Purpose                                           |
| :------------------- | :------------------------------------------------ |
| `telemetry_runs`     | One row per analysis/fixer/patch run              |
| `telemetry_events`   | Granular events within a run (issues, LLM calls)  |

Tables are auto-created by `PostgresDbSetup` during database initialization, or can be manually applied via `db/schema_telemetry.sql`.

### Dashboard

The **Telemetry** tab in the Streamlit UI displays: total runs, issues found/fixed, fix success rate, runs over time, issues by severity, top issue types, LLM usage by model, and a drill-down into individual run events.

---

## Human-in-the-Loop (HITL) Feedback Store

The HITL pipeline uses **PostgreSQL** (shared `codebase_analytics_db`) for persistent storage of human feedback decisions and constraint rules. This enables agents to learn from accumulated human review history across runs.

### Database tables

| Table                       | Purpose                                     |
| :-------------------------- | :------------------------------------------ |
| `hitl_feedback_decisions`   | Human review outcomes (SKIP, FIX, etc.)     |
| `hitl_constraint_rules`     | Parsed constraint rules from markdown files |
| `hitl_run_metadata`         | Audit trail of analysis runs                |

### Enabling HITL

HITL can be enabled via the CLI (`--enable-hitl`), `global_config.yaml` (`hitl.enable: true`), or the **Enable HITL** toggle in the Streamlit sidebar. When enabled, agents will check past feedback decisions and inject constraint-aware context into LLM prompts.

---

## Troubleshooting

### Memory Optimization

```bash
# Memory-optimized run for large codebases
python main.py --codebase-path ./codebase \
  --max-files 1000 --batch-size 50 --memory-limit 3000

# Debug mode with monitoring
python main.py --debug --enable-memory-monitoring --max-files 500
```

### Database Maintenance

```bash
psql -h localhost -U codebase_analytics_user -d codebase_analytics_db
```

```sql
-- Check embeddings
SELECT document, cmetadata FROM langchain_pg_embedding LIMIT 5;

-- Check telemetry runs
SELECT run_id, mode, status, issues_total, issues_fixed, duration_seconds
FROM telemetry_runs ORDER BY created_at DESC LIMIT 10;

-- Check HITL feedback history
SELECT issue_type, human_action, COUNT(*) FROM hitl_feedback_decisions
GROUP BY issue_type, human_action ORDER BY count DESC;

-- Clear all vector data
DELETE FROM langchain_pg_embedding;
DELETE FROM langchain_pg_collection;
```

### Common Issues

- **`ModuleNotFoundError: No module named 'networkx'`**: Run `pip install -r requirements.txt` to install all dependencies.
- **Adapters show "tool not available"**: Install optional tools — `pip install lizard flawfinder`. For CCLS adapters, ensure `ccls` is installed and in PATH.
- **CCLS indexing timeout**: Increase `dependency_builder.indexing_timeout_seconds` in `global_config.yaml`.
- **LLM provider errors**: Verify `--llm-model` uses the correct `provider::model` format and that the corresponding API key is set.

---

## General debug - find dependency issues
``` bash
 
 python -c 'import sys; sys.path.append("."); from utils.common.email_reporter import EmailReporter; print("Success!")'
 python -c 'import sys; sys.path.append("."); from utils.common.excel_writer import ExcelWriter; print("Success!")'
 python -c 'import sys; sys.path.append("."); from utils.common.llm_tools import LLMTools; print("Success!")'
 

  export LIBCLANG_PATH=/opt/homebrew/Cellar/llvm/21.1.8/lib/libclang.dylib 
```


For Error initializing libclang: /usr/lib/llvm-14/lib/libclang.so: undefined symbol: clang_CXXMethod_isDeleted. Please ensure that your python bindings are compatible with your libclang.so version.

``` bash 
pip uninstall -y clang libclang
pip install clang==14.*
``
 

 ```

## Context-Aware LLM Analysis (Header Context Injection)

CURE's LLM-exclusive analysis now automatically resolves `#include` directives and injects relevant type definitions from header files into each code chunk sent to the LLM. This significantly reduces false positives by giving the LLM visibility into enum ranges, macro constants, struct layouts, typedefs, and function signatures that were previously invisible.

### Problem

Without header context, the LLM frequently flags valid code as problematic because it cannot see definitions from included headers. Common false positives include enum-bounded array accesses flagged as out-of-bounds, macro-defined buffer sizes flagged as unchecked, struct field accesses flagged as invalid, and known function return types misinterpreted.

### How It Works

The `HeaderContextBuilder` module (`agents/context/header_context_builder.py`) operates in three phases:

1. **Include Resolution**: Parses `#include` directives from the source file and recursively resolves them to actual header file paths (configurable depth, default 2 levels). System headers (`<stdio.h>`, etc.) are excluded by default since LLMs already understand standard library types.

2. **Header Parsing**: Extracts definitions from each resolved header using regex patterns — enums (with member values and auto-increment tracking), structs/unions (with field types and array sizes), `#define` macros (with numeric value evaluation), typedefs, function prototypes, and extern variable declarations. Results are cached per header file for the entire analysis run.

3. **Relevance Filtering**: For each code chunk, the builder identifies which definitions are actually referenced (via identifier matching) and assembles a concise context string. A priority system (enums > macros > structs > typedefs > protos > externs) ensures the most impactful definitions fit within the configurable token budget.

### What Gets Injected

The context is injected above each code chunk in the LLM prompt:

```c
// ──── HEADER CONTEXT (from included headers) ────
// Enums:
enum wifi_band { WIFI_BAND_2G = 0, WIFI_BAND_5G = 1, WIFI_BAND_6G = 2, WIFI_BAND_MAX = 3 };

// Macros:
#define MAX_CHANNELS 64
#define BUF_SIZE 4096

// Structs:
struct channel_info { uint8_t band; uint16_t freq; int8_t power; uint32_t flags; };

// Function prototypes:
int wifi_validate_channel(struct channel_info *info, enum wifi_band band);
// ──── END HEADER CONTEXT ────
```

### False Positive Categories Addressed

| Pattern | Root Cause | How Context Fixes It |
|:--------|:-----------|:---------------------|
| Enum-indexed array flagged as OOB | LLM cannot see enum range | Enum definition shows MAX value matches array size |
| Macro-bounded buffer flagged as unchecked | LLM cannot see `#define` value | Macro injection shows numeric constant |
| Struct field access flagged as invalid | LLM cannot see struct layout | Struct definition shows valid fields |
| `sizeof(struct)` flagged as wrong | LLM does not know struct size | Full struct layout provided |
| Function return used without NULL check | LLM does not know return type | Prototype shows `int` return (non-pointer) |
| Typedef'd type misunderstood | LLM does not know underlying type | `typedef uint32_t status_t;` resolves ambiguity |
| `ARRAY_SIZE` macro not recognized | LLM sees unknown macro | Macro definition injected |
| Conditional compilation flagged as dead code | LLM does not understand `#ifdef` | Prompt rules cover this pattern |
| Bit flags used as array indices | LLM confuses flags with indices | Enum with hex values shows bit flag pattern |

### Configuration

Add to `global_config.yaml` (enabled by default):

```yaml
context:
  enable_header_context: true
  include_paths: []              # Additional -I style paths (relative to codebase root)
  max_header_depth: 2            # How deep to follow #include chains (0 = direct only)
  max_context_chars: 6000        # Max chars for header context per chunk (~1500 tokens)
  exclude_system_headers: true   # Skip <stdio.h>, <stdlib.h>, etc.
```

### Key Design Decisions

- **No CCLS required**: Works entirely via regex-based parsing. When CCLS is also enabled, both context sources complement each other (CCLS provides call graphs, HeaderContext provides type definitions).
- **Cached per run**: Each header is parsed once and reused across all files and chunks in the analysis run.
- **Token budget aware**: The `max_context_chars` limit ensures header context does not consume too much of the LLM's input window. Priority ordering ensures the most impactful definitions (enums, macros) are included first.
- **Backward compatible**: If disabled or if the module fails to import, the pipeline runs exactly as before.

### Files

```text
agents/context/
├── __init__.py
└── header_context_builder.py    # Include resolution, header parsing, context assembly
```

Modified: `agents/codebase_llm_agent.py` (integration), `prompts/codebase_analysis_prompt.py` (9 context-aware rules), `global_config.yaml` (configuration).

---

## File Discovery Exclusions

CURE provides flexible file exclusion controls via both `global_config.yaml` and CLI flags. Exclusions apply across the entire pipeline — file discovery, LLM analysis, CCLS indexing, header context resolution, and deep static adapters.

### Configuration

Add permanent exclusions in `global_config.yaml`:

```yaml
scanning:
  # Directory names to skip during file discovery (matched by name, not path).
  # These are added on top of the built-in defaults (.git, build, node_modules, etc.)
  exclude_dirs:
    - test
    - third_party
    - vendor

  # Glob patterns to skip (matched against the relative path from codebase root).
  # Patterns are case-insensitive and use fnmatch syntax.
  exclude_globs:
    - "*/test/*"
    - "*/generated/*"
    - "moc_*.cpp"
    - "*_autogen/*"
```

Both options accept multiple entries as YAML lists. CLI flags `--exclude-dirs` and `--exclude-globs` are **merged** with these config values (not replaced), allowing you to define permanent exclusions in config while adding ad-hoc ones on the command line:

```bash
# Config defines exclude_dirs: [test, vendor]
# CLI adds "docs" → final list: [test, vendor, docs]
python main.py --llm-exclusive --codebase-path ./project --exclude-dirs docs
```

Duplicates are automatically removed. Config entries take precedence in ordering, followed by CLI additions.

### Where Exclusions Apply

| Component | `exclude_dirs` | `exclude_globs` |
|:----------|:---------------|:----------------|
| File discovery (`FileProcessor`) | Skips directories by name | Skips files matching glob against relative path |
| `CodebaseLLMAgent` (`_gather_files`) | Skips directories by name | fnmatch filtering on relative paths |
| `HeaderContextBuilder` (include resolution) | Skips directories during recursive header search | Skips resolved headers matching glob patterns |
| `CCLSIngestion` (`.ccls` config) | Converted to `%ignore .*/<dir>/.*` regex patterns | Converted to `%ignore` regex patterns |
| Streamlit UI | Text input field (comma-separated) | Text input field (comma-separated) |

### Built-in Defaults

The following directories are always excluded from file discovery, regardless of configuration: `.git`, `build`, `node_modules`, `.venv`, `__pycache__`, `dist`, `.ccls-cache`, `bin`, `obj`, and others. The `exclude_dirs` config and CLI options add to these built-in defaults.

---

## Automated Constraint Generation & Context Validation

CURE includes two tools that automatically reduce false positives in LLM-based analysis by extracting codebase-wide symbol knowledge and tracing validation patterns at the per-chunk level.

### Tool 1: Codebase Constraint Generator

`agents/context/codebase_constraint_generator.py` scans the entire codebase and extracts enums, structs, macros, bit-field patterns, and helper/validator functions, then generates a `codebase_constraints.md` file with IGNORE rules for common false positive categories.

**What it generates:**

| Category | Example Rule |
|:---------|:-------------|
| Enum-bounded arrays | IGNORE bounds check when array sized to `MAX_QUEUES` and indexed by `enum queue_type` |
| Hardware-init structs | IGNORE NULL checks for `struct dp_soc`, `struct dp_pdev` (allocated at init, never NULL) |
| Macro-defined limits | IGNORE bounds warnings where index compared against `MAX_RINGS`, `BUF_SIZE`, etc. |
| Bitmask operations | IGNORE "suspicious bit manipulation" for `_MASK`/`_SHIFT`/`_BIT` macros |
| Validator functions | IGNORE missing validation if `check_*()` or `validate_*()` called upstream |
| Chained dereferences | IGNORE intermediate NULL checks when root pointer (`soc->pdev->ops`) is validated |

**CLI usage:**

```bash
# Standalone — generates codebase_constraints.md and exits
python main.py --generate-constraints --codebase-path /path/to/src

# Or run the script directly
python agents/context/codebase_constraint_generator.py \
  --codebase-path /path/to/src \
  --output agents/constraints/codebase_constraints.md \
  --exclude-dirs build,vendor
```

The generated file is automatically loaded by `CodebaseLLMAgent` alongside `common_constraints.md` and file-specific constraint files.

**Custom constraint files:** You can supply additional constraint `.md` files via `--include-custom-constraints`. These are loaded after the auto-generated codebase constraints and before file-specific constraints, allowing project-specific or team-specific rules:

```bash
python main.py --llm-exclusive --codebase-path /path/to/src \
  --include-custom-constraints my_team_rules.md /shared/security_constraints.md
```

Paths can be absolute or relative (resolved against CWD first, then `agents/constraints/`). The constraint loading order is: `common_constraints.md` → `codebase_constraints.md` (auto-generated) → custom constraint files → `<filename>_constraints.md` (file-specific).

**Streamlit UI:** The Constraints tab includes an "Auto-Generate Codebase Constraints" expander that runs the generator and provides a download button.

### Tool 2: Context Validator (Per-Chunk Pre-Analysis)

`agents/context/context_validator.py` runs inline during LLM analysis. For each code chunk, it strips comments, traces pointer validations, array bounds, return-value checks, and chained dereferences using regex heuristics, then injects a compact validation summary into the prompt before the LLM sees the code. Multi-line function signatures are fully supported.

**Statuses:** `VALIDATED` (explicit check found), `CALLER_CHECKED` (function parameter — caller responsible), `BOUNDED` (compile-time or runtime bound), `LOCALLY_ALLOCATED` (dynamic alloc — must be checked), `NOT_CHECKED` (no validation found — FLAG).

**What it traces:**

| Check Type | Heuristics |
|:-----------|:-----------|
| Pointer null-checks | Local allocation detection (FLAG), null-check in scope (IGNORE), `IS_ERR`/`IS_ERR_OR_NULL` kernel macros, `BUG_ON`/`WARN_ON`/`assert` macros, ternary check (`ptr ? ... : ...`), function parameter — both static and non-static (CALLER_CHECKED → IGNORE), struct member chain inheritance, file-level backward check |
| Array bounds | Loop-bound (`for i < LIMIT`), explicit comparison (`if idx < MAX`), modulo (`idx % SIZE`), macro constant index (`arr[MAX_QUEUES]` — compile-time), `sizeof`/`ARRAY_SIZE`/`NELEMS` bound, `clamp`/`min`/`max` bound, `switch(idx)` case-bounded, enum type inference, function parameter as index (CALLER_CHECKED → IGNORE), file-level backward bounds check |
| Return values | Immediate null/error check, guard pattern, `IS_ERR`/`IS_ERR_OR_NULL` kernel macros, negative error codes (`ret < 0`, `ret != 0`, `ret == -EINVAL`), `BUG_ON`/`WARN_ON`/`assert` macros, ternary inline check, `(void)func()` intentional discard |
| Chained dereferences | Root pointer VALIDATED or CALLER_CHECKED → entire chain (`soc->pdev->ops->callback`) inherits IGNORE |

**Comment stripping:** Single-line (`//`) and block (`/* */`) comments are stripped before identifier extraction to prevent false positives from comment text.

**Keyword/macro exclusion:** Common C macros (`min`, `max`, `IS_ERR`, `memcpy`, `printk`, `snprintf`, etc.) are excluded from pointer/return-value analysis to prevent false flags on macro calls.

**Per-chunk output injected into prompt:**

```c
// ── CONTEXT VALIDATION (pre-analysis) ──────────────
// Pointers:
//   soc              -> CALLER_CHECKED (param of static dp_peer_setup() — caller validates)
//   pdev             -> CALLER_CHECKED (param of static dp_peer_setup() — caller validates)
//   peer             -> VALIDATED (null-checked in current chunk)
//   buf              -> LOCALLY_ALLOCATED (kzalloc line 155) — FLAG if unchecked
// Array Bounds:
//   MAX_RINGS        -> BOUNDED (macro constant: MAX_RINGS)
//   i                -> BOUNDED (comparison: i < ARRAY_SIZE)
//   type             -> BOUNDED (switch-case on type)
//   ring_idx         -> BOUNDED (clamp/min/max bound for ring_idx)
// Return Values:
//   dp_peer_alloc()  -> VALIDATED (IS_ERR/IS_ERR_OR_NULL check for peer)
//   dp_peer_register() -> VALIDATED (assert/BUG_ON check for ret)
// Chained Derefs:
//   soc->pdev->ops   -> VALIDATED (root `soc` caller_checked — chain inherits)
// ── END VALIDATION ─────────────────────────────────
```

The context validator works entirely via regex heuristics (no CCLS required). When CCLS is available, it can optionally use call-hierarchy data for upstream pointer tracing. If the validator fails or is unavailable, the pipeline continues without it.

### Tool 3: Static Call Stack Analyzer (Cross-Function Tracing)

`agents/context/static_call_stack_analyzer.py` builds a codebase-wide function index at startup, then for each code chunk traces every pointer, array index, divisor, enum, and macro through the call chain to find where values are set, validated, or constrained. This deep call-chain evidence is injected as Context Layer 4 alongside the existing header context and validation context.

**What it traces:**

| Category | Trace Method |
|:---------|:-------------|
| Pointer dereferences (`ptr->x`) | Walk reverse call graph, check null_checks in each caller's function body |
| Array indices (`arr[idx]`) | Check loop bounds, enum type, comparison guards, modulo ops in current func + callers |
| Divisions (`a / b`) | Trace divisor through assignments and caller parameters for non-zero guarantee |
| Enum usage (`switch(val)`) | Resolve enum type and report full range from codebase index |
| Macro values (`BUF_SIZE`) | Resolve numeric/string value from HeaderContextBuilder cache |
| Loop bounds (`i < limit`) | Trace limit through assignments, macros, enum members, caller parameters |

**Per-chunk output injected into prompt:**

```c
// ──── CALL STACK CONTEXT ────────────────────────────────
// Pointers:
//   req            -> NULL-checked in caller handle_msg() at L245
//   ctx            -> Param of static func; caller validates
//   buf            -> Allocated (kmalloc), needs null check
// Array Bounds:
//   idx            -> Bounded: for-loop i < MAX_ENTRIES (=64)
//   queue_id       -> Enum dp_queue_type range [0..7]
// Division Safety:
//   count          -> Guaranteed non-zero: checked in caller
// Macros: BUF_SIZE=4096, MAX_RETRIES=10
// ──── END CALL STACK CONTEXT ────────────────────────────
```

The analyzer works in two modes: regex-only (always available, no CCLS required) and CCLS-enhanced (uses LSP call hierarchy when available). Index building happens once at startup (~2-5 seconds for 100K LOC), per-chunk analysis takes <50ms.

### Files

```text
agents/context/
├── __init__.py
├── header_context_builder.py         # Include resolution, header parsing, context assembly
├── codebase_constraint_generator.py  # Tool 1: symbol extraction + constraint rule generation
├── context_validator.py              # Tool 2: per-chunk validation context builder
└── static_call_stack_analyzer.py     # Tool 3: codebase-wide call chain tracing

agents/constraints/
├── codebase_constraints.md           # Auto-generated output (after running Tool 1)
├── common_constraints.md             # Manual global rules
├── TEMPLATE_constraints.md           # Template for per-file constraints
└── GENERATE_CONSTRAINTS_PROMPT.md    # LLM prompt for constraint generation
```

Modified: `agents/codebase_llm_agent.py` (ContextValidator integration, `codebase_constraints.md` loading, custom constraint loading, StaticCallStackAnalyzer integration), `main.py` (`--generate-constraints` flag, `--include-custom-constraints` flag), `ui/app.py` (auto-generate button in Constraints tab, custom constraint file input), `ui/background_workers.py` (custom constraints wiring).

---

## Contributing

Contributions are welcome! Please open issues and pull requests for any improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE).
# CURE
