from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.upload import router as upload_router

app = FastAPI(
    title="Multiagent Data Analytics & BI Engine API",
    description="High-performance backend orchestrating LangGraph multi-agent swarm with DuckDB and Supabase.",
    version="0.1.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(upload_router)


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
