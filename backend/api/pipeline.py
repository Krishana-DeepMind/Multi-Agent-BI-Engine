import asyncio
import json
import logging
import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Optional
import redis.asyncio as aioredis

from backend.core.database import get_latest_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

redis_url = os.getenv("UPSTASH_REDIS_URL", "redis://localhost:6379")
redis_client = aioredis.from_url(redis_url)

# In-memory session store (will be replaced by Supabase DB layer in Days 8-9)
session_store: Dict[str, Dict] = {}

# All pipeline statuses from AgentSwarmState.pipeline_status Literal
PIPELINE_STEPS = [
    ("routing",   "Intent Router analyzing query..."),
    ("ingesting", "Loading file into DuckDB and profiling schema..."),
    ("cleaning",  "Data Cleaning Agent applying quality fixes..."),
    ("featuring", "Feature Architect generating derived columns..."),
    ("querying",  "Analytics Engine executing DuckDB queries..."),
    ("layouting", "Layout Agent generating ECharts configurations..."),
    ("verifying", "QA Agent validating pipeline output..."),
]


async def emit_status(session_id: str, status: str, message: str, agent: str = "", extra: Optional[Dict] = None):
    """Push a status event into the session's SSE queue."""
    event = {
        "status": status,
        "message": message,
        "agent": agent,
    }
    if extra:
        event.update(extra)
    await redis_client.publish(f"session:{session_id}:status", json.dumps(event))


async def run_pipeline_task(session_id: str):
    """
    Background task that orchestrates the pipeline execution.
    
    Currently uses the mock LLMRouter for the router_node and simulates
    subsequent steps. Real LangGraph orchestration will be wired in 
    when the remaining agents are built (Days 10-12).
    """
    try:
        session = session_store.get(session_id)
        if not session:
            await emit_status(session_id, "failed", "Session not found.", agent="system")
            await redis_client.publish(f"session:{session_id}:status", json.dumps({"status": "close"}))
            return

        # --- Emit file metadata so the frontend can display it ---
        await emit_status(
            session_id, "initiated", "Session loaded. Starting pipeline...",
            agent="system",
            extra={
                "file_name": session.get("file_name", session.get("file_path", "unknown")),
                "row_count": session.get("row_count", 0),
                "col_count": session.get("col_count", 0),
                "file_type": session.get("file_type", "csv"),
            }
        )

        # --- Step 1: Router Node (real call) ---
        await emit_status(session_id, "routing", "Intent Router analyzing query...", agent="router")
        
        try:
            from backend.agents.router_node import router_node
            from backend.core.state import AgentSwarmState, QAReport
            import uuid
            from datetime import datetime, timezone

            # Build a minimal AgentSwarmState from session data
            initial_state = AgentSwarmState(
                session_id=uuid.UUID(session_id) if len(session_id) == 36 else uuid.uuid4(),
                user_id=session.get("user_id", "anonymous"),
                pipeline_status="initiated",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
                current_agent="system",
                raw_query=session.get("raw_query", "Analyze this dataset"),
                intent_class="trend_analysis",
                business_domain="unknown",
                key_entities=[],
                time_dimension=None,
                raw_file_path=session.get("file_path", ""),
                file_type=session.get("file_type", "csv"),
                raw_row_count=session.get("row_count", 0),
                raw_col_count=session.get("col_count", 0),
                schema_fingerprint="",
                schema_embedding_id=None,
                column_metadata=[],
                similar_schemas_found=False,
                cleaning_operations=[],
                cleaned_parquet_path="",
                data_quality_score=1.0,
                rows_before=session.get("row_count", 0),
                rows_after=session.get("row_count", 0),
                columns_dropped=[],
                feature_definitions=[],
                enriched_parquet_path="",
                feature_rationale="",
                generated_queries=[],
                query_results=[],
                queries_failed=[],
                dashboard_config=[],
                dashboard_title="",
                dashboard_theme="dark",
                layout_rationale="",
                qa_report=QAReport(
                    data_quality_score=0.0,
                    completeness_score=0.0,
                    query_validity={},
                    chart_relevance={},
                    anomalies=[],
                    suggestions=[],
                    overall_confidence=0.0,
                    approval_status="needs_review",
                    reviewer_notes=None,
                ),
                errors=[],
                retry_count=0,
                token_usage={},
            )

            routed_state = await router_node(initial_state)
            
            await emit_status(
                session_id, "routing", 
                f"Intent classified: {routed_state.intent_class} | Domain: {routed_state.business_domain}",
                agent="router",
                extra={
                    "intent_class": routed_state.intent_class,
                    "business_domain": routed_state.business_domain,
                    "key_entities": routed_state.key_entities,
                    "time_dimension": routed_state.time_dimension,
                }
            )
        except Exception as e:
            logger.error(f"Router node failed: {e}")
            await emit_status(session_id, "routing", f"Router completed with fallback: {e}", agent="router")

        # --- Steps 2-7: Remaining agents (simulated until built) ---
        for status, message in PIPELINE_STEPS[1:]:
            await asyncio.sleep(1.5)
            await emit_status(session_id, status, message, agent=status.rstrip("ing") + "ing")

        # --- Final: Complete ---
        await asyncio.sleep(0.5)
        await emit_status(session_id, "complete", "Pipeline finished successfully.", agent="system")

    except Exception as e:
        logger.error(f"Pipeline failed for session {session_id}: {e}")
        await emit_status(session_id, "failed", f"Pipeline error: {str(e)}", agent="system")
    finally:
        # Send sentinel to close the SSE stream
        await redis_client.publish(f"session:{session_id}:status", json.dumps({"status": "close"}))


@router.post("/{session_id}/start")
async def start_pipeline(session_id: str, background_tasks: BackgroundTasks, raw_query: str = "Analyze this dataset"):
    """
    Start the pipeline for a given session.
    Creates session state, loads file to DuckDB (stubbed), and kicks off
    LangGraph execution asynchronously via BackgroundTasks.
    """
    # Create session record if it doesn't exist
    if session_id not in session_store:
        session_store[session_id] = {
            "session_id": session_id,
            "user_id": "anonymous",
            "raw_query": raw_query,
            "file_path": "",
            "file_type": "csv",
            "row_count": 0,
            "col_count": 0,
            "status": "initiated",
        }

    # Kick off background pipeline task
    background_tasks.add_task(run_pipeline_task, session_id)

    return {
        "message": "Pipeline started successfully",
        "session_id": session_id,
        "status": "initiated",
    }


@router.get("/{session_id}/stream")
async def stream_pipeline_status(session_id: str):
    """
    Server-Sent Events (SSE) endpoint that streams pipeline status updates.
    Polls the Redis pub/sub queue.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"session:{session_id}:status")

    async def event_generator():
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"].decode("utf-8")
                    if data == '{"status": "close"}':
                        break
                    yield f"data: {data}\n\n"
        finally:
            await pubsub.unsubscribe(f"session:{session_id}:status")
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/{session_id}/state")
async def get_pipeline_state(session_id: str):
    """
    Fetch the latest state snapshot from checkpoints.
    """
    checkpoint = await get_latest_checkpoint(session_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="No pipeline state found for this session.")
    return {
        "session_id": session_id,
        "agent": checkpoint.agent_name,
        "state": checkpoint.state_json,
        "checkpoint_at": checkpoint.checkpoint_at
    }
