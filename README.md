# Multi-Agent Data Analytics & BI Engine

Welcome to the Multi-Agent Data Analytics & BI Engine. This project uses an advanced LangGraph-powered agent swarm alongside DuckDB, Polars, and Supabase to orchestrate fully autonomous business intelligence pipelines from raw datasets.

## Architecture

- **Backend**: FastAPI
- **Database**: Supabase (PostgreSQL 15) with `pgvector`
- **Analytics Engine**: DuckDB + Polars
- **LLM Routing**: Dynamic router optimizing for Groq, Gemini, and Ollama (Local)
- **Frontend**: Next.js 14 (React)

## Environment Setup

### 1. Python Backend

```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt
```

Set up your `.env` file in the root directory or `backend/.env`:

```env
GROQ_API_KEY=your_groq_key
GOOGLE_API_KEY=your_google_key
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_key
UPSTASH_REDIS_URL=redis://localhost:6379  # Or your Upstash Redis URL
DATABASE_URL=postgresql+asyncpg://user:password@host/dbname
OLLAMA_URL=http://localhost:11434
```

Ensure Ollama is running with the required models:
```bash
ollama pull qwen2.5-coder:7b
ollama pull llama3.2:latest
ollama pull nomic-embed-text:latest
```

Start the backend:
```bash
uvicorn backend.main:app --reload --port 8000
```

### 2. Next.js Frontend

```bash
cd frontend
npm install

# Set up local environment variables (.env.local)
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Start the frontend:
```bash
npm run dev
```

## Running the End-to-End Pipeline

1. **Upload**: Drop a CSV/Parquet file via the frontend.
2. **Analysis**: The pipeline streams status updates through Server-Sent Events (SSE) while the agents perform schema inference and cleaning.
3. **Dashboards**: Final output is presented in an interactive ECharts dashboard.

## Development & Testing

We provide a comprehensive pytest suite to validate pipeline nodes and routing logic.
To execute tests:
```bash
pytest backend/tests/
```

## Sprint 1 Complete
This repository represents the completed codebase for Sprint 1 ("The Skeleton").
