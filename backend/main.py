import os
from dotenv import load_dotenv

# Load .env from the backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.core.database import create_tables
from backend.api.upload import router as upload_router
from backend.api.pipeline import router as pipeline_router
from backend.api.dashboards import router as dashboards_router
from backend.api.cleaning_demo import router as cleaning_demo_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await create_tables()
    except Exception as e:
        print(f"Database table initialization warning: {e}")
    yield

app = FastAPI(
    title="Multiagent Data Analytics & BI Engine API",
    description="High-performance backend orchestrating LangGraph multi-agent swarm with DuckDB and Supabase.",
    version="0.1.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(upload_router)
app.include_router(pipeline_router)
app.include_router(dashboards_router)
app.include_router(cleaning_demo_router)


@app.get("/", tags=["health"])
async def root():
    return {
        "status": "online",
        "service": "Multiagent BI Engine API",
        "version": "0.1.0"
    }


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "healthy"}
