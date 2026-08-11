# Multiagent BI Engine Project

This directory contains the source code for the Multiagent Data Analytics & BI Engine.
The project consists of:
- `backend/`: A FastAPI backend that orchestrates the LangGraph agents.
- `frontend/`: A Next.js 14 frontend that provides the UI and dashboard.

## Setup Instructions
- Python requirements are in `backend/requirements.txt`
- Frontend dependencies are defined in `frontend/package.json`
- `.env` template is at `backend/.env`
- `.env.local` template is at `frontend/.env.local`

## Progress & Completed Work
### Sprint 1: Day 1 (Environment Bootstrap)
- Created the master folder structure for both `backend` and `frontend`.
- Initialized Python backend with `venv` and `requirements.txt`. Installed core dependencies (LangGraph, FastAPI, DuckDB, Polars, etc.).
- Scaffolded Next.js 14 frontend using `create-next-app`.
- Initialized `shadcn-ui` and installed necessary charts and state libraries (`echarts`, `@tremor/react`, `zustand`).
- Setup boilerplate page routing (`/upload`, `/session/[sessionId]/configure`, `/dashboard/[sessionId]`, etc.).
- Created configuration templates `.env` and `.env.local`.
- Added `.gitignore` and pushed initial commit to GitHub repository (`https://github.com/Krishana-DeepMind/Multi-Agent-BI-Engine`).

### Sprint 1: Day 2 (State Contract & Database Schema)
- Implemented the complete `AgentSwarmState` as a Pydantic v2 model with validators in `backend/core/state.py`.
- Implemented sub-types including `ColumnMeta`, `CleaningOperation`, `FeatureDefinition`, `QueryDefinition`, `QueryResult`, `ChartConfig`, and `QAReport`.
- Implemented `ContextSlicer` for passing only required state slices to LLM agents in `backend/core/context_slicer.py`.
- Implemented `schema_compressor.py` with `compress_column_meta_for_prompt()` to reduce schema tokens passed to agents.
- Created Supabase database schema and migrations for `sessions`, `pipeline_states`, `dashboards`, and `schema_embeddings` using pgvector in `supabase/migrations/001_initial_schema.sql`.

### Sprint 1: Day 3 (File Upload Infrastructure)
- Implemented `file_validator.py` with magic byte validation (Parquet `PAR1`, Excel PK/ZIP, JSON structures, CSV text) and fast row count estimation.
- Created `DuckDBEngine` in `backend/core/duckdb_engine.py` with in-memory execution, extensions, Supabase signed URL dataset loader, `get_schema_profile()`, `get_statistical_summary()`, `execute_validated()`, and `write_to_parquet()`.
- Implemented `POST /api/upload` router in `backend/api/upload.py` supporting multipart uploads up to 100MB with streaming to Supabase Storage and PostgreSQL session persistence.
- Created FastAPI app entrypoint `backend/main.py` with CORS, routing, and health checks.
- Built Next.js 14 drag-and-drop file uploader in `frontend/src/app/upload/page.tsx` with dark engineering aesthetic, react-dropzone, live axios progress bar, Shadcn alert error handling, and redirection.
- Added comprehensive pytest suite (15/15 tests passing) and validated Next.js build compilation.

### Sprint 1: Day 4 (Intent Router Node & Pipeline API)
- Created `ROUTER_SYSTEM_PROMPT` in `backend/prompts/router.txt` covering 7 intent classes and 8 business domains.
- Built mock `LLMRouter` in `backend/core/llm_router.py` with full `TaskType` enum (12 members), smart keyword-based classification with priority ordering (correlation before comparison, HR before sales), entity extraction, and regex time parsing.
- Implemented `router_node()` LangGraph node function in `backend/agents/router_node.py` with `ContextSlicer` integration, JSON response parsing (clean, markdown-fenced, partial), and state mutation.
- Built pipeline API in `backend/api/pipeline.py` with `POST /start` (session creation, real `router_node` invocation via `BackgroundTasks`) and `GET /stream` (SSE endpoint emitting all 7 pipeline statuses: routing → ingesting → cleaning → featuring → querying → layouting → verifying → complete).
- Registered `pipeline_router` in `backend/main.py` with clean top-level imports.
- Created 26 parametrized pytest cases in `backend/tests/unit/test_router.py` across 5 categories (finance, sales, HR, ecommerce, edge) with intent/domain assertions, <800ms latency checks, and an aggregate 88% accuracy gate — achieved 100% (25/25). All 41 tests passing.

### Sprint 1: Day 5 (DuckDB Integration & Schema Profile)
- Built `SchemaProfiler` in `backend/core/schema_profiler.py` using batch `DESCRIBE`, `COUNT`, and `APPROX_COUNT_DISTINCT` queries to extract `null_pct`, `unique_pct`, `sample_values`, and `is_primary_key` heuristic. Returns `List[ColumnMeta]` for the `AgentSwarmState`.
- Enhanced `DuckDBEngine` with `jsonl`/`ndjson` format support in both `load_from_bytes` and `load_from_supabase`. Installed `fastexcel` for full Excel parsing.
- Created 7 comprehensive tests in `backend/tests/unit/test_schema_profiler.py` covering 6 file types: clean CSV, messy CSV (mixed types), Excel (.xlsx with merged cells via openpyxl), nested JSON, Parquet, and JSON Lines. 50MB benchmark (1M rows) loads + profiles in 0.11s (target was <5s).
- Built custom Tailwind `Stepper` component in `frontend/src/components/ui/stepper.tsx` with animated active step (pulse + ring glow), completed checkmarks, and connecting lines.
- Created Zustand store in `frontend/src/store/pipeline-store.ts` managing SSE connection, step mapping, live log accumulation, and session metadata (fileName, rowCount, colCount, fileType).
- Implemented `/session/[sessionId]/configure` page with 4-card file metadata grid, 6-step stepper bound to SSE status, dark-themed "Agent Swarm Activity Log" terminal with auto-scroll, and conditional "View Dashboard" button. Pipeline emits file metadata in initial SSE event.
- All 48 backend tests passing, Next.js build compiles with 0 TypeScript errors.
