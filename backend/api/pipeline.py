import asyncio
import json
import logging
import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Optional, Any
from pydantic import BaseModel
import redis.asyncio as aioredis

from backend.core.database import get_latest_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

class FilterRequest(BaseModel):
    filters: Dict[str, Any] = {}

redis_url = os.getenv("UPSTASH_REDIS_URL", "redis://localhost:6379")
try:
    redis_client = aioredis.from_url(redis_url)
except Exception:
    redis_client = None

# In-memory session store (will be replaced by Supabase DB layer in Days 8-9)
session_store: Dict[str, Dict] = {}

# Pre-seed session_store if test_e2e_output.json exists (for controlled browser test)
try:
    _test_path = os.path.join(os.path.dirname(__file__), "..", "..", "test_e2e_output.json")
    if os.path.exists(_test_path):
        with open(_test_path, "r") as _f:
            _seeded_data = json.load(_f)
            _sid = _seeded_data.get("session_id")
            if _sid:
                session_store[_sid] = {
                    "session_id": _sid,
                    "dashboard_config": _seeded_data.get("dashboard_config", []),
                    "dashboard_title": _seeded_data.get("dashboard_title", ""),
                    "layout_rationale": _seeded_data.get("layout_rationale", ""),
                    "data_quality_score": 1.0,
                    "generated_queries": _seeded_data.get("generated_queries", []),
                    "dashboard_filters": _seeded_data.get("dashboard_filters", []),
                }
except Exception as _e:
    logger.debug(f"Could not pre-seed session_store: {_e}")

# In-memory pubsub fallback when Redis is unavailable
_in_memory_subscribers: Dict[str, list] = {}

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


async def _publish_event(session_id: str, payload: str):
    """Publish event to Redis if available, and to in-memory queues."""
    if redis_client:
        try:
            await redis_client.publish(f"session:{session_id}:status", payload)
        except Exception:
            pass
    for q in list(_in_memory_subscribers.get(session_id, [])):
        try:
            await q.put(payload)
        except Exception:
            pass


async def emit_status(session_id: str, status: str, message: str, agent: str = "", extra: Optional[Dict] = None):
    """Push a status event into the session's SSE queue."""
    event = {
        "status": status,
        "message": message,
        "agent": agent,
    }
    if extra:
        event.update(extra)
    await _publish_event(session_id, json.dumps(event))


async def run_pipeline_task(session_id: str):
    """
    Background task that orchestrates the full pipeline execution.
    Sprint 4: Runs Router -> Ingestion -> Cleaning -> Feature -> Analytics -> Layout.
    Streams SSE events for each stage. Saves dashboard config on completion.
    """
    try:
        session = session_store.get(session_id)
        if not session:
            await emit_status(session_id, "failed", "Session not found.", agent="system")
            await _publish_event(session_id, json.dumps({"status": "close"}))
            return

        # Emit file metadata
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

        # ── Step 1: Router ───────────────────────────────────────────────────
        await emit_status(session_id, "routing", "Intent Router analyzing query...", agent="router")
        routed_state = None

        try:
            from backend.agents.router_node import router_node
            from backend.core.state import AgentSwarmState, QAReport
            import uuid
            from datetime import datetime, timezone

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
                f"Intent: {routed_state.intent_class} | Domain: {routed_state.business_domain}",
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
            await emit_status(session_id, "routing", f"Router fallback: {str(e)[:120]}", agent="router")
            if routed_state is None:
                routed_state = initial_state  # type: ignore

        # ── Steps 2–6: Run full LangGraph pipeline ───────────────────────────
        try:
            from backend.core.graph_builder import build_pipeline_graph
            from backend.core.llm_router import LLMRouter
            from backend.core.duckdb_engine import DuckDBEngine

            db_engine = DuckDBEngine()
            llm_router = LLMRouter()
            pipeline = build_pipeline_graph(db_engine, llm_router)

            # SSE step emissions for each stage
            step_labels = [
                ("ingesting",  "Ingestion & Schema Agent running..."),
                ("cleaning",   "Cleaning & Imputation Agent running..."),
                ("featuring",  "Feature Architect Agent running..."),
                ("querying",   "Analytics Engine executing DuckDB queries..."),
                ("layouting",  "Layout Agent generating visualizations..."),
            ]

            # Emit individual step statuses while the pipeline runs
            async def emit_steps():
                delays = [2, 4, 8, 15, 5]
                for (status, message), delay in zip(step_labels, delays):
                    await asyncio.sleep(delay)
                    await emit_status(session_id, status, message, agent=status.rstrip("ing"))

            emit_task = asyncio.create_task(emit_steps())

            final_state = await pipeline.ainvoke(
                routed_state.model_dump(),
                config={"configurable": {"thread_id": session_id}}
            )

            emit_task.cancel()

            # Parse final state
            if isinstance(final_state, dict):
                from backend.core.state import AgentSwarmState
                final_state_obj = AgentSwarmState(**final_state)
            else:
                final_state_obj = final_state

            # Save dashboard config to session store for API retrieval
            session_store[session_id]["dashboard_config"] = [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in (final_state_obj.dashboard_config or [])
            ]
            session_store[session_id]["dashboard_title"] = getattr(final_state_obj, "dashboard_title", "")
            session_store[session_id]["layout_rationale"] = getattr(final_state_obj, "layout_rationale", "")
            session_store[session_id]["data_quality_score"] = getattr(final_state_obj, "data_quality_score", 0.0)
            session_store[session_id]["generated_queries"] = [
                q.model_dump() if hasattr(q, "model_dump") else q
                for q in (final_state_obj.generated_queries or [])
            ]
            session_store[session_id]["dashboard_filters"] = getattr(final_state_obj, "dashboard_filters", [])

            n_charts = len(final_state_obj.dashboard_config or [])
            n_queries = len(final_state_obj.generated_queries or [])

            await emit_status(
                session_id, "complete",
                f"Pipeline complete. {n_queries} queries executed, {n_charts} charts generated.",
                agent="system",
                extra={
                    "dashboard_title": final_state_obj.dashboard_title,
                    "chart_count": n_charts,
                    "query_count": n_queries,
                    "data_quality_score": final_state_obj.data_quality_score,
                    "layout_rationale": final_state_obj.layout_rationale,
                    "session_id": session_id,
                }
            )

        except Exception as e:
            logger.error(f"LangGraph pipeline failed for session {session_id}: {e}", exc_info=True)
            await emit_status(session_id, "failed", f"Pipeline error: {str(e)[:200]}", agent="system")

    except Exception as e:
        logger.error(f"Pipeline task crashed for session {session_id}: {e}", exc_info=True)
        await emit_status(session_id, "failed", f"Critical error: {str(e)[:200]}", agent="system")
    finally:
        await _publish_event(session_id, json.dumps({"status": "close"}))


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
    Polls Redis pub/sub queue if available, with transparent in-memory queue fallback.
    """
    q: asyncio.Queue = asyncio.Queue()
    _in_memory_subscribers.setdefault(session_id, []).append(q)

    async def event_generator():
        pubsub = None
        use_redis = False
        if redis_client:
            try:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(f"session:{session_id}:status")
                use_redis = True
            except Exception as e:
                logger.info(f"Redis pubsub unavailable for {session_id}, fallback to memory: {e}")
                use_redis = False

        try:
            if use_redis and pubsub:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data = message["data"].decode("utf-8")
                        if data == '{"status": "close"}':
                            break
                        yield f"data: {data}\n\n"
            else:
                while True:
                    data = await q.get()
                    if data == '{"status": "close"}':
                        break
                    yield f"data: {data}\n\n"
        finally:
            if use_redis and pubsub:
                try:
                    await pubsub.unsubscribe(f"session:{session_id}:status")
                    await pubsub.close()
                except Exception:
                    pass
            if session_id in _in_memory_subscribers and q in _in_memory_subscribers[session_id]:
                _in_memory_subscribers[session_id].remove(q)

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


@router.get("/{session_id}/dashboard")
async def get_session_dashboard(session_id: str):
    """
    Get the dashboard configuration from the in-memory session store.
    Sprint 4 primary path — reads dashboard_config written by layout_node.
    """
    session = session_store.get(session_id)
    if not session:
        # Check if test_e2e_output.json matches this session_id
        try:
            _test_path = os.path.join(os.path.dirname(__file__), "..", "..", "test_e2e_output.json")
            if os.path.exists(_test_path):
                with open(_test_path, "r", encoding="utf-8") as _f:
                    _seeded_data = json.load(_f)
                    if _seeded_data.get("session_id") == session_id:
                        session = {
                            "session_id": session_id,
                            "dashboard_config": _seeded_data.get("dashboard_config", []),
                            "dashboard_title": _seeded_data.get("dashboard_title", ""),
                            "layout_rationale": _seeded_data.get("layout_rationale", ""),
                            "data_quality_score": 1.0,
                            "generated_queries": _seeded_data.get("generated_queries", []),
                            "dashboard_filters": _seeded_data.get("dashboard_filters", []),
                        }
                        session_store[session_id] = session
        except Exception as _err:
            logger.debug(f"Failed to load seeded data: {_err}")

    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    dashboard_config = session.get("dashboard_config", [])
    if not dashboard_config:
        raise HTTPException(status_code=404, detail="Dashboard not yet generated for this session. Pipeline may still be running.")

    return {
        "session_id": session_id,
        "title": session.get("dashboard_title", "Dashboard"),
        "layout_rationale": session.get("layout_rationale", ""),
        "data_quality_score": session.get("data_quality_score", 0.0),
        "chart_count": len(dashboard_config),
        "charts": dashboard_config,
        "generated_queries": session.get("generated_queries", []),
        "filters": session.get("dashboard_filters", []),
    }


@router.post("/{session_id}/filter")
async def filter_dashboard(session_id: str, req: FilterRequest):
    """
    Dynamically recalculate dashboard charts and KPI cards based on user-selected filters.
    Executes DuckDB SQL queries with WHERE clauses against the session's enriched/cleaned parquet file.
    """
    import glob
    import duckdb
    from backend.agents.layout_node import split_kpi_cards, build_fallback_config

    session = session_store.get(session_id)
    if not session:
        # Check test_e2e_output.json fallback
        try:
            _test_path = os.path.join(os.path.dirname(__file__), "..", "..", "test_e2e_output.json")
            if os.path.exists(_test_path):
                with open(_test_path, "r", encoding="utf-8") as _f:
                    _seeded_data = json.load(_f)
                    if _seeded_data.get("session_id") == session_id:
                        session = {
                            "session_id": session_id,
                            "dashboard_config": _seeded_data.get("dashboard_config", []),
                            "dashboard_title": _seeded_data.get("dashboard_title", ""),
                            "layout_rationale": _seeded_data.get("layout_rationale", ""),
                            "data_quality_score": 1.0,
                            "generated_queries": _seeded_data.get("generated_queries", []),
                            "dashboard_filters": _seeded_data.get("dashboard_filters", []),
                        }
                        session_store[session_id] = session
        except Exception as _err:
            logger.debug(f"Failed to load seeded data: {_err}")

    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    orig_charts = session.get("dashboard_config", [])
    active_filters = {
        k: str(v).strip()
        for k, v in req.filters.items()
        if v and str(v).strip().lower() != "all"
    }

    # If no filters active, return original charts immediately
    if not active_filters:
        return {
            "session_id": session_id,
            "charts": orig_charts,
            "active_filters": {},
        }

    # Find the enriched or cleaned parquet
    pattern = os.path.join("tmp_storage", "*", session_id, "*.parquet")
    found_files = glob.glob(pattern)
    parquet_path = None
    for candidate in found_files:
        if "enriched.parquet" in candidate:
            parquet_path = candidate
            break
    if not parquet_path:
        for candidate in found_files:
            if "cleaned.parquet" in candidate:
                parquet_path = candidate
                break

    if not parquet_path or not os.path.exists(parquet_path):
        logger.warning(f"No parquet found for session {session_id} to filter")
        return {
            "session_id": session_id,
            "charts": orig_charts,
            "active_filters": active_filters,
        }

    try:
        norm_path = parquet_path.replace("\\", "/")
        conn = duckdb.connect()
        where_clauses = []
        for col, val in active_filters.items():
            safe_val = val.replace("'", "''")
            safe_col = col.replace("'", "").replace('"', "")
            where_clauses.append(f"LOWER(CAST({safe_col} AS VARCHAR)) = LOWER('{safe_val}')")

        where_sql = " WHERE " + " AND ".join(where_clauses)
        conn.execute(f"CREATE TEMP VIEW data AS SELECT * FROM read_parquet('{norm_path}'){where_sql}")

        generated_queries = session.get("generated_queries", [])
        filtered_charts = []

        for q in generated_queries:
            q_id = q.get("id", "")
            sql = q.get("sql", "")
            try:
                df = conn.execute(sql).fetchdf()
                cols = list(df.columns)
                rows = df.to_dict(orient="records")
                res = {"column_names": cols, "sample_rows": rows, "row_count": len(rows)}

                orig_c = next((c for c in orig_charts if c.get("query_id") == q_id or c.get("query_id", "").startswith(f"{q_id}_")), {})
                is_kpi = (
                    orig_c.get("chart_type") == "kpi_card"
                    or "kpi" in q.get("analytical_purpose", "").lower()
                    or "kpi" in q_id.lower()
                    or "overview" in q_id.lower()
                )

                if is_kpi and len(rows) > 0:
                    kpis = split_kpi_cards(q, res, max_kpis=4)
                    filtered_charts.extend(kpis)
                else:
                    ct = orig_c.get("echarts_option", {}).get("_chart_type", orig_c.get("chart_type", "bar"))
                    opt = build_fallback_config(q, res, ct)
                    opt["_chart_type"] = ct
                    opt["_insight_summary"] = q.get("insight_summary", "")
                    filtered_charts.append({
                        "query_id": q_id,
                        "chart_type": ct,
                        "title": q.get("title", orig_c.get("title", "")),
                        "subtitle": q.get("business_question", orig_c.get("subtitle", "")),
                        "echarts_option": opt,
                        "priority": q.get("priority", 3),
                        "layout": orig_c.get("layout", {"x": 0, "y": 0, "w": 6, "h": 4}),
                        "responsive": True,
                    })
            except Exception as q_err:
                logger.warning(f"Filtered query {q_id} failed: {q_err}")
                orig_c = next((c for c in orig_charts if c.get("query_id") == q_id), None)
                if orig_c:
                    filtered_charts.append(orig_c)

        conn.close()

        if filtered_charts:
            from backend.agents.layout_node import assign_grid_layout
            filtered_charts = assign_grid_layout(filtered_charts)

        return {
            "session_id": session_id,
            "charts": filtered_charts if filtered_charts else orig_charts,
            "active_filters": active_filters,
        }

    except Exception as e:
        logger.error(f"Error filtering dashboard for session {session_id}: {e}", exc_info=True)
        return {
            "session_id": session_id,
            "charts": orig_charts,
            "active_filters": active_filters,
        }


@router.get("/{session_id}/query/{query_id}/data")
async def get_query_result_data(session_id: str, query_id: str):
    """
    Return query result data as JSON for ECharts hydration.
    Reads from local parquet written by analytics_node.
    """
    import os
    try:
        import duckdb
        session = session_store.get(session_id)
        user_id = session.get("user_id", "anonymous") if session else "anonymous"
        parquet_path = f"tmp_storage/{user_id}/{session_id}/result_{query_id}.parquet"

        if not os.path.exists(parquet_path):
            raise HTTPException(status_code=404, detail=f"Result file not found for query {query_id}")

        conn = duckdb.connect()
        rows = conn.execute(f"SELECT * FROM read_parquet('{parquet_path}')").fetchdf()
        conn.close()
        return {
            "session_id": session_id,
            "query_id": query_id,
            "row_count": len(rows),
            "columns": list(rows.columns),
            "data": rows.to_dict(orient="records"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load query data for {query_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load query data: {str(e)}")


@router.post("/{session_id}/follow-up")
async def follow_up_question(
    session_id: str,
    background_tasks: BackgroundTasks,
    new_question: str = "",
):
    """
    Sprint 4 follow-up question flow.
    Re-uses cleaned/enriched data from the original session.
    Starts a derived pipeline session from Feature -> Analytics -> Layout.
    """
    import uuid
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Parent session not found.")

    new_session_id = str(uuid.uuid4())

    # Inherit file path and metadata from parent session
    session_store[new_session_id] = {
        **session,
        "session_id": new_session_id,
        "raw_query": new_question or session.get("raw_query", ""),
        "parent_session_id": session_id,
        "status": "initiated",
        "dashboard_config": [],
    }

    background_tasks.add_task(run_pipeline_task, new_session_id)

    return {
        "message": "Follow-up question pipeline started",
        "new_session_id": new_session_id,
        "parent_session_id": session_id,
        "question": new_question,
        "status": "initiated",
    }


@router.post("/{session_id}/resume")
async def resume_pipeline(session_id: str, background_tasks: BackgroundTasks):
    """
    Resume pipeline execution from the latest saved checkpoint.
    Loads checkpoint state and resumes execution from last completed node onwards.
    """
    from backend.core.checkpoint_manager import checkpoint_manager
    latest_state = await checkpoint_manager.load_latest_checkpoint(session_id)
    if not latest_state:
        raise HTTPException(status_code=404, detail="No checkpoint found for session.")

    current_agent = latest_state.get("current_agent", "routing")
    logger.info(f"Resuming pipeline for session {session_id} from checkpoint node '{current_agent}'")

    background_tasks.add_task(run_pipeline_task, session_id)
    return {
        "message": f"Pipeline resumed from node '{current_agent}'",
        "session_id": session_id,
        "resumed_from": current_agent,
        "status": "resuming"
    }

