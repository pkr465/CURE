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
12. **Batch Patch Agent**: Applies multi-file patches (with `===` file headers) to a local codebase, producing patched copies in `out/patched_files/` with folder structure preserved.

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
│   ├── codebase_batch_patch_agent.py    # Batch multi-file patch application (=== header format)
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
│   ├── context/                        # Context layers for LLM analysis
│   │   ├── __init__.py
│   │   ├── header_context_builder.py   #   Include resolution, header parsing, context assembly
│   │   ├── context_validator.py        #   Per-chunk pointer/bounds/return validation
│   │   ├── static_call_stack_analyzer.py #  Cross-function call chain tracing
│   │   ├── function_param_validator.py #   Per-function parameter validation context
│   │   └── codebase_constraint_generator.py # Auto-generate constraint rules from symbols
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
│   ├── telemetry_service.py            # Telemetry pipeline (per-finding, per-LLM-call, cost, constraints)
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
│   ├── codebase_analysis_prompt.py     # LLM analysis prompt template
│   └── patch_review_prompt.py          # Patch review prompt template
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



**Install PostgreSQL and initialize the database** (required for vector DB pipeline, telemetry, and HITL):

#### Option A — Local Database (automated bootstrap)

**macOS / Linux:**
```bash
sudo ./bootstrap_db.sh
```
The script auto-detects the installed PostgreSQL version, installs pgvector (building from source if the Homebrew bottle doesn't match your PG version), creates the user, database, extension, and permissions.

**Windows (PowerShell — run as Administrator):**
```powershell
.\bootstrap_db.ps1
```

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

#### Option B — Remote Database Server

Both bootstrap scripts support connecting to a remote PostgreSQL server for centralized telemetry, run history, and team-wide analytics. When a remote host is detected, the scripts skip local PostgreSQL installation and connect directly.

**macOS / Linux:**
```bash
DB_HOST=db.example.com DB_PORT=5432 \
  DB_ADMIN_USER=admin DB_ADMIN_PASSWORD=secretpass \
  DB_USER=codebase_analytics_user DB_PASSWORD=postgres \
  DB_NAME=codebase_analytics_db \
  DB_SSL_MODE=require \
  sudo -E ./bootstrap_db.sh
```

**Windows PowerShell:**
```powershell
$env:DB_HOST="db.example.com"
$env:DB_PORT="5432"
$env:DB_ADMIN_USER="admin"
$env:DB_ADMIN_PASSWORD="secretpass"
$env:DB_SSL_MODE="require"
.\bootstrap_db.ps1
```

The bootstrap script performs a pre-flight connectivity check (`pg_isready`) before proceeding. For SSL modes `verify-ca` or `verify-full`, supply `DB_SSL_CA`, `DB_SSL_CERT`, and `DB_SSL_KEY` pointing to your certificate files.

#### Environment Variable Overrides

All options install PostgreSQL with pgvector (local) or connect to existing PostgreSQL (remote), create the application user and database, enable the vector extension, and grant all required permissions. Defaults match `global_config.yaml`. Override with environment variables:

| Variable | Default | Description |
|:---------|:--------|:------------|
| `DB_HOST` | `localhost` | PostgreSQL host (remote host skips local install) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `codebase_analytics_db` | Database name |
| `DB_USER` | `codebase_analytics_user` | Application user |
| `DB_PASSWORD` | `postgres` | Application user password |
| `DB_ADMIN_USER` | *(auto)* | Admin user for remote DB setup |
| `DB_ADMIN_PASSWORD` | *(none)* | Admin password for remote DB setup |
| `DB_SSL_MODE` | `prefer` | SSL mode: `disable`, `prefer`, `require`, `verify-ca`, `verify-full` |
| `DB_SSL_CA` | *(none)* | Path to CA certificate file |
| `DB_SSL_CERT` | *(none)* | Path to client certificate file |
| `DB_SSL_KEY` | *(none)* | Path to client private key file |

```bash
# Quick local override example
DB_USER=myuser DB_PASSWORD=mypass DB_NAME=mydb sudo -E ./bootstrap_db.sh
```

#### Connection Pool & SSL Configuration

For production deployments, tune connection pooling and SSL in `global_config.yaml`:

```yaml
database:
  pool_size: 5              # Persistent connections in the pool
  pool_recycle: 3600        # Seconds before recycling a connection
  pool_timeout: 30          # Seconds to wait for a pool connection
  pool_pre_ping: true       # Verify connections before use (recommended)

  ssl_mode: prefer          # disable | prefer | require | verify-ca | verify-full
  ssl_ca: ""                # Path to CA certificate
  ssl_cert: ""              # Path to client certificate
  ssl_key: ""               # Path to client private key
```

These settings apply to both TelemetryService and all database operations. For remote servers, `ssl_mode: require` or higher is recommended.

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
| `--exclude-headers H [H]`      | Header files to exclude from context injection (exact names, basenames, or glob patterns) |
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
| `--batch-patch PATCH_FILE`            | Run batch-patch mode: apply a multi-file patch (=== header format) instead of the fixer                  |
| `--patch-file PATH`                   | Path to a `.patch`/`.diff` file for single-file patch analysis (requires `--patch-target`)               |
| `--patch-target PATH`                 | Path to the original source file being patched (used with `--patch-file`)                                |
| `--patch-codebase-path PATH`          | Root of the codebase for header/context resolution during patch analysis                                 |
| `--enable-adapters`                   | Enable deep static analysis adapters (Lizard, Flawfinder, CCLS) for patch analysis                      |
| `--codebase-path PATH`                | Root directory of the source code                                                                        |
| `--out-dir DIR`                       | Directory for output/patched files                                                                       |
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

### Patch Analysis (single-file)

```bash
# Analyse a patch against the original file
python fixer_workflow.py --patch-file fix.patch --patch-target src/module.c

# With codebase context for header resolution
python fixer_workflow.py --patch-file fix.patch --patch-target src/module.c \
  --codebase-path /path/to/project

# With deep static adapters
python fixer_workflow.py --patch-file fix.patch --patch-target src/module.c \
  --enable-adapters
```

### Batch Patch (multi-file)

```bash
# Apply a multi-file patch (=== header format)
python fixer_workflow.py --batch-patch t.patch

# With explicit codebase path
python fixer_workflow.py --batch-patch t.patch --codebase-path /path/to/project
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
| `database`           | PostgreSQL connection, PGVector collection, pool tuning, SSL/TLS |
| `email`              | SMTP report delivery configuration                    |
| `scanning`           | File discovery exclusions — directory names and glob patterns to skip |
| `dependency_builder` | CCLS executable, timeouts, BFS depth, connection pool |
| `excel`              | Report styling (colors, column widths, freeze/filter) |
| `mermaid`            | Diagram rendering configuration                       |
| `hitl`               | HITL RAG pipeline — feedback store, constraint parsing |
| `context`            | Header context injection — include paths, depth, token budget |
| `telemetry`          | Telemetry pipeline — enable/disable, cost tracking, usage reports |
| `logging`            | Log level, verbose/debug flags                        |

---

## Telemetry & Analytics

CURE includes a comprehensive, fire-and-forget telemetry pipeline that records granular framework usage patterns into PostgreSQL (`codebase_analytics_db`). Every public logging method swallows exceptions silently so telemetry never disrupts the analysis pipeline. Telemetry is enabled by default and can be toggled in `global_config.yaml` or the Streamlit sidebar.

### What Is Tracked

| Category | Details |
|:---------|:--------|
| **Run summaries** | Mode (analysis/fixer/patch), status, duration, file count, issue counts by severity |
| **Per-finding detail** | File path, line range, title, category, severity, confidence, description, suggestion, code snippet, fixed code, false-positive flag, user feedback, and arbitrary JSONB metadata |
| **Per-LLM-call detail** | Provider, model, purpose (analysis/fix/patch_review), file path, chunk index, prompt tokens, completion tokens, total tokens, latency in milliseconds, estimated cost in USD, status, and error messages |
| **Constraint effectiveness** | Which constraint file/rule was applied, which file and issue type it affected, and the action taken (modified prompt or suppressed finding) |
| **Static analysis results** | Per-adapter results (complexity, security, dead code, call graph, function metrics) with file counts and JSONB details per run |
| **Fix outcomes** | Issues fixed, skipped, and failed per fixer run with per-item audit trail |
| **HITL decisions** | Constraint hits from human-in-the-loop feedback — prompt augmentations and issue suppressions |
| **Usage reports** | Materialized daily/weekly summaries of runs, files, findings, fixes, tokens, and estimated cost (upsert pattern) |

### LLM Cost Estimation

The telemetry service includes a built-in token pricing table that automatically estimates cost for each LLM call based on the provider and model. The pricing table covers Anthropic Claude (Sonnet, Opus, Haiku), Google Vertex AI (Gemini), and Azure OpenAI (GPT-4.1). Cost is computed as `(prompt_tokens × input_price + completion_tokens × output_price) / 1,000,000` and stored alongside each call record.

Supported models and their pricing (per million tokens):

| Provider | Model | Input | Output |
|:---------|:------|------:|-------:|
| Anthropic | claude-sonnet-4 | $3.00 | $15.00 |
| Anthropic | claude-opus-4 | $15.00 | $75.00 |
| Anthropic | claude-haiku-4 | $0.25 | $1.25 |
| Vertex AI | gemini-2.5-pro | $1.25 | $10.00 |
| Azure | gpt-4.1 | $2.00 | $8.00 |

Unknown models default to a conservative $5.00/$15.00 per million tokens. The pricing table is easily extensible in `db/telemetry_service.py`.

### Configuration

```yaml
# global_config.yaml
telemetry:
  enable: true   # set to false to disable all telemetry

database:
  pool_size: 5              # Persistent connections in the pool
  pool_recycle: 3600        # Seconds before recycling a connection
  pool_timeout: 30          # Seconds to wait for a pool connection
  pool_pre_ping: true       # Verify connections before use
  ssl_mode: prefer          # disable | prefer | require | verify-ca | verify-full
  ssl_ca: ""                # Path to CA certificate
  ssl_cert: ""              # Path to client certificate
  ssl_key: ""               # Path to client private key
```

Pool configuration is passed to SQLAlchemy's `create_engine()` and applies to all telemetry database operations. For remote PostgreSQL servers, set `ssl_mode: require` or higher.

### Database Tables

| Table | Purpose |
|:------|:--------|
| `telemetry_runs` | One row per analysis/fixer/patch run — mode, status, duration, file counts, severity breakdown |
| `telemetry_events` | Legacy granular events within a run (issues, LLM calls) — retained for backward compatibility |
| `telemetry_findings` | Per-finding detail — file, lines, title, category, severity, confidence, code snippets, fix status, false-positive flag, user feedback, JSONB metadata |
| `telemetry_llm_calls` | Per-LLM-call detail — provider, model, purpose, tokens, latency, estimated cost, status |
| `telemetry_constraint_hits` | Constraint rule applications — source file, rule name, target file, issue type, action (modified/suppressed) |
| `telemetry_static_analysis` | Per-adapter static analysis results — adapter name, files analyzed, issues found, JSONB details |
| `telemetry_usage_reports` | Materialized daily/weekly usage summaries with unique constraint on (report_date, report_type) for upsert |

All tables use `run_id` foreign keys back to `telemetry_runs`. Tables are auto-created by `PostgresDbSetup` during database initialization and by `TelemetryService._init_schema()` on first connection. The full schema can also be applied manually via `db/schema_telemetry.sql`.

### Agent Telemetry Integration

All three agents (`CodebaseLLMAgent`, `CodebaseFixerAgent`, `CodebasePatchAgent`) are instrumented with per-call LLM telemetry. Each `llm_tools.llm_call()` invocation is timed and logged with token counts extracted from the response object. The background workers in `ui/background_workers.py` additionally log per-finding detail, per-adapter static analysis results, and severity breakdowns at the end of each run.

Constraint tracking is wired into both the LLM agent and patch agent at three points: when HITL feedback suppresses a file (`should_skip_issue`), when HITL augments a prompt (`augment_prompt`), and when constraint files inject identification rules into the prompt. Each event is logged to `telemetry_constraint_hits` with the constraint source (file names or `hitl_feedback`), rule type, target file, and action taken.

### Dashboard

The **Telemetry** tab in the Streamlit UI provides a 5-tab analytics dashboard:

| Tab | Contents |
|:----|:---------|
| **📊 Overview** | Top-level metrics (runs, issues, fixes, fix rate, estimated 30-day cost), runs-over-time bar chart, daily cost trend line chart, issues by severity, top issue types, LLM usage summary, and a recent runs table with drill-down into individual run events |
| **🔍 Detailed Findings** | Filterable findings explorer with run, severity, and category filters. Shows false-positive rate (30-day), confirmed vs. flagged counts, per-finding detail table, and CSV export button |
| **🤖 LLM Analytics** | Cost breakdown by provider/model (bar chart + table), token efficiency by model, and per-run LLM call detail with latency distribution histogram. Per-run summary shows total calls, tokens, and cost |
| **🛡️ Constraints & Quality** | Constraint hit summary by rule (table), actions breakdown (bar chart), agent comparison (30-day), and false-positive rate detail (JSON view) |
| **📋 Usage Reports** | Daily/weekly materialized report viewer with generate button, tabular display of runs/files/findings/fixes/tokens/cost, and CSV export |

### TelemetryService API

The `TelemetryService` class (`db/telemetry_service.py`) exposes both logging and query methods:

**Logging methods** (fire-and-forget, all swallow exceptions):

| Method | Purpose |
|:-------|:--------|
| `start_run()` | Create a new telemetry run record, returns `run_id` |
| `finish_run()` | Update run with final status, counts, duration, severity breakdown |
| `log_event()` | Legacy event logging (backward compatible) |
| `log_finding()` | Log a single finding with full detail (file, lines, severity, code, fix status) |
| `log_llm_call_detailed()` | Log a single LLM call with tokens, latency, and auto-computed cost |
| `log_constraint_hit()` | Log a constraint rule application (source, rule, file, action) |
| `log_static_analysis()` | Log adapter results (adapter name, files, issues, JSONB details) |
| `generate_usage_report()` | Materialize a daily/weekly usage summary (upsert) |

**Query methods** (for dashboard):

| Method | Returns |
|:-------|:--------|
| `get_summary_stats()` | Aggregate metrics, runs by date, severity distribution, top issue types |
| `get_cost_summary(days)` | Total cost, daily trend, and per-model cost breakdown |
| `get_findings_detail(run_id)` | All findings for a run (or all runs if `None`) |
| `get_constraint_effectiveness(run_id)` | Constraint hits grouped by rule and by action |
| `get_false_positive_rate(days)` | Total findings, false positives, confirmed, and rate |
| `get_agent_comparison(days)` | Per-agent run counts, issue counts, fix rates, avg duration |
| `get_usage_reports(report_type, limit)` | Materialized usage report records |
| `get_recent_runs(limit)` | Most recent run records |
| `get_run_events(run_id)` | Legacy events for a specific run |

### Querying Telemetry Directly

```sql
-- Cost summary by model (last 30 days)
SELECT model, COUNT(*) as calls,
       SUM(total_tokens) as tokens,
       SUM(estimated_cost_usd) as cost
FROM telemetry_llm_calls
WHERE created_at > NOW() - INTERVAL '30 days'
GROUP BY model ORDER BY cost DESC;

-- False positive rate
SELECT COUNT(*) as total,
       SUM(CASE WHEN is_false_positive THEN 1 ELSE 0 END) as false_positives,
       ROUND(100.0 * SUM(CASE WHEN is_false_positive THEN 1 ELSE 0 END) / COUNT(*), 1) as fp_rate
FROM telemetry_findings
WHERE created_at > NOW() - INTERVAL '30 days';

-- Constraint effectiveness
SELECT constraint_source, constraint_rule, action, COUNT(*)
FROM telemetry_constraint_hits
GROUP BY constraint_source, constraint_rule, action
ORDER BY count DESC;

-- Recent runs with cost
SELECT r.run_id, r.mode, r.status, r.issues_total, r.duration_seconds,
       COALESCE(SUM(l.estimated_cost_usd), 0) as run_cost
FROM telemetry_runs r
LEFT JOIN telemetry_llm_calls l ON l.run_id = r.run_id
GROUP BY r.run_id, r.mode, r.status, r.issues_total, r.duration_seconds
ORDER BY r.created_at DESC LIMIT 10;
```

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

-- Check telemetry runs with cost
SELECT r.run_id, r.mode, r.status, r.issues_total, r.duration_seconds,
       COALESCE(SUM(l.estimated_cost_usd), 0) as run_cost
FROM telemetry_runs r
LEFT JOIN telemetry_llm_calls l ON l.run_id = r.run_id
GROUP BY r.run_id, r.mode, r.status, r.issues_total, r.duration_seconds
ORDER BY r.created_at DESC LIMIT 10;

-- Check findings by severity
SELECT severity, COUNT(*) FROM telemetry_findings
GROUP BY severity ORDER BY count DESC;

-- Check LLM cost by model
SELECT model, COUNT(*) as calls, SUM(total_tokens) as tokens,
       SUM(estimated_cost_usd) as cost
FROM telemetry_llm_calls GROUP BY model ORDER BY cost DESC;

-- Check HITL feedback history
SELECT issue_type, human_action, COUNT(*) FROM hitl_feedback_decisions
GROUP BY issue_type, human_action ORDER BY count DESC;

-- Check constraint effectiveness
SELECT constraint_source, action, COUNT(*)
FROM telemetry_constraint_hits
GROUP BY constraint_source, action ORDER BY count DESC;

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
  exclude_headers: []            # Project headers to exclude (exact names, basenames, or globs)
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

### Header Exclusions

Specific header files can be excluded from context injection using the `exclude_headers` config key or the `--exclude-headers` CLI flag. This is useful for excluding large auto-generated headers, third-party headers, or headers that cause noise in the analysis. Supports exact names, basenames, and fnmatch glob patterns:

```yaml
context:
  exclude_headers:
    - "auto_generated.h"      # Exact name match
    - "debug_*.h"             # Glob pattern
    - "third_party/vendor.h"  # Path-based match
```

```bash
python main.py --llm-exclusive --codebase-path ./project \
  --exclude-headers "auto_generated.h" "debug_*.h"
```

Config and CLI values are merged (not replaced). Excluded headers are skipped during include resolution in `HeaderContextBuilder` and will not appear in the context injected into LLM prompts.

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

### Tool 4: Function Parameter Validator (Per-Function Param Context)

`agents/context/function_param_validator.py` analyzes each function definition within a code chunk and reports whether each parameter has been validated — null checks for pointers, bounds checks for indices, switch/enum range checks, and struct field access patterns. This context is injected as a C-comment block so the LLM knows which parameters are already guarded.

**What it checks per parameter:**

| Parameter Type | Check | Status |
|:---------------|:------|:-------|
| Pointer (`*ptr`) | `if (!ptr)`, `if (ptr == NULL)` | `NULL_CHECKED at line N` |
| Index (`int idx`) | `if (idx < MAX)`, `for (i < N)` | `BOUNDS_CHECKED (idx < MAX)` |
| Enum (`enum cmd_type cmd`) | `switch (cmd)` | `SWITCH_CHECKED` |
| Struct pointer | `ptr->field` access patterns | `FIELDS_ACCESSED: field1, field2` |
| Any (caller-side) | Null check at call site | `CALLER_CHECKED` |

**Per-chunk output injected into prompt:**

```c
// ── FUNCTION PARAMETER VALIDATION ──
// void process_frame(struct sk_buff* skb, int idx, enum cmd_type cmd):
//   skb (struct sk_buff*): VALIDATED [NULL_CHECKED at line 105, FIELDS_ACCESSED: data, len]
//   idx (int): VALIDATED [BOUNDS_CHECKED (idx >= MAX_FRAMES)]
//   cmd (enum cmd_type): VALIDATED [SWITCH_CHECKED]
// ── END PARAMETER VALIDATION ──
```

The validator is integrated into all three agents (LLM, Patch, Fixer) and runs after the call-stack analyzer. It uses only regex — no CCLS required. If the module fails to import, the pipeline continues without it.

### Files

```text
agents/context/
├── __init__.py
├── header_context_builder.py         # Include resolution, header parsing, context assembly
├── codebase_constraint_generator.py  # Tool 1: symbol extraction + constraint rule generation
├── context_validator.py              # Tool 2: per-chunk validation context builder
├── static_call_stack_analyzer.py     # Tool 3: codebase-wide call chain tracing
└── function_param_validator.py       # Tool 4: per-function parameter validation context

agents/constraints/
├── codebase_constraints.md           # Auto-generated output (after running Tool 1)
├── common_constraints.md             # Manual global rules
├── TEMPLATE_constraints.md           # Template for per-file constraints
└── GENERATE_CONSTRAINTS_PROMPT.md    # LLM prompt for constraint generation
```

Modified: `agents/codebase_llm_agent.py` (ContextValidator, StaticCallStackAnalyzer, FunctionParamValidator integration, `codebase_constraints.md` loading, custom constraint loading), `agents/codebase_patch_agent.py` (all context layers, patched file preservation, empty line filtering), `agents/codebase_fixer_agent.py` (FunctionParamValidator integration), `main.py` (`--generate-constraints`, `--include-custom-constraints`, `--exclude-headers` flags), `ui/app.py` (auto-generate button in Constraints tab, custom constraint file input), `ui/background_workers.py` (custom constraints wiring).

---

## Patch Agent Features

The `CodebasePatchAgent` (`agents/codebase_patch_agent.py`) analyzes code patches (unified, context, normal, or combined diff formats) and reports only issues introduced by the patch — not pre-existing bugs.

### Patched File Preservation

After analysis, the patched file is automatically saved to `./out/patched_files/<filename>` so it can be inspected or used for further processing. The file path is included in the return dict as `patched_file_path`.

### Empty Line Filtering

Empty and whitespace-only lines within patch hunks are excluded from `>>>` marker tagging. This prevents the LLM from wasting attention on blank lines and reduces noise in the analysis.

### Static Analysis Adapters

The patch agent runs all 5 deep static adapters on both original and patched files for apples-to-apples diffing: `ast_complexity`, `security`, `dead_code`, `call_graph`, and `function_metrics`. Results scoped to the patch hunk ranges are written to `patch_static_<name>` tabs in the Excel output.

### Defensive Programming Rules

Both the LLM analysis prompt and patch review prompt include strict rules preventing the LLM from flagging defensive programming patterns as bugs — redundant null checks, multi-layer bounds validation, switch default branches, and defense-in-depth patterns are all explicitly excluded from issue reporting.

---

## Batch Patch Agent

The `CodebaseBatchPatchAgent` (`agents/codebase_batch_patch_agent.py`) applies multi-file patches to a local codebase, producing patched copies in `out/patched_files/` with the codebase's folder structure preserved. It is designed for Perforce/depot-style patch files that contain diffs for many files in a single file. It can be invoked standalone or via `fixer_workflow.py --batch-patch`.

### Patch File Format

The agent expects a multi-file patch with `===` headers separating each file's diff:

```
=== //depot/path/to/file.h#641 — /local/mnt/workspace/path/to/file.h
2524c2524,2525
<     A_UINT32 txop_us);
---
>     A_UINT32 txop_us,
>     wal_pdev_t *pdev);
=== //depot/path/to/cfg.c#805 — /local/mnt/workspace/path/to/cfg.c
1589c1589,1591
<     .opt.threshold = 5000,
---
>     .opt.threshold = 10000,
>     .opt.reg_domain = 6000,
>     .opt.max_limit = 12000,
```

Each section has a header with the server (depot) path and local path separated by ` — `, followed by normal or unified diff hunks.

### Usage

```bash
# Via fixer_workflow.py (recommended — resolves config automatically)
python fixer_workflow.py --batch-patch t.patch
python fixer_workflow.py --batch-patch t.patch --codebase-path /path/to/codebase
python fixer_workflow.py --batch-patch t.patch --dry-run

# Standalone
python agents/codebase_batch_patch_agent.py --patch-file t.patch
python agents/codebase_batch_patch_agent.py --patch-file t.patch --codebase-path /path/to/codebase
python agents/codebase_batch_patch_agent.py --patch-file t.patch --dry-run
```

### CLI Options

| Flag | Description |
| :--- | :--- |
| `--patch-file FILE` | Path to the multi-file patch (required) |
| `--codebase-path PATH` | Root directory of source code (defaults to `global_config.yaml` `paths.code_base_path`) |
| `--out-dir DIR` | Output directory (default: `./out`) |
| `--config-file PATH` | Path to custom `global_config.yaml` |
| `--dry-run` | Show what would be patched without writing files |
| `--verbose` | Enable verbose output |

### Output Structure

```
out/
  patched_files/
    components/rel/.../sched_algo/sched_algo.h        ← patched
    components/rel/.../sched_algo/sched_algo_cfg.c     ← patched
    components/rel/.../sched_algo/sched_algo_cfg.h     ← patched
```

The agent supports both normal diff (`NUMcNUM`, `NUMaNUM`, `NUMdNUM`) and unified diff (`@@`) formats, auto-detecting per file section. Files that cannot be found in the codebase are skipped with a warning — the agent does not abort on missing files.

---

## Contributing

Contributions are welcome! Please open issues and pull requests for any improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE).
# CURE
