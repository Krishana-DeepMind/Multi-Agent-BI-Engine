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

