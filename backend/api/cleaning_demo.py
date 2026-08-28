"""
Cleaning Agent Demo API
=======================
A self-contained FastAPI router that runs the REAL cleaning pipeline
locally (no Supabase, no Redis) and streams results via SSE.

Endpoints:
  POST /api/cleaning/run          — Upload file, start pipeline job
  GET  /api/cleaning/{job_id}/stream   — SSE event stream
  GET  /api/cleaning/{job_id}/download — Download cleaned CSV
"""

import asyncio
import io
import json
import logging
import os
import uuid
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import polars as pl
from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cleaning", tags=["cleaning_demo"])

# ---------------------------------------------------------------------------
# In-memory job store
# job_id -> {
#   "status": "running"|"done"|"error",
#   "queue":  asyncio.Queue of SSE event dicts,
#   "events": list of all past events (for late-joiners),
#   "cleaned_csv_bytes": bytes | None,
# }
# ---------------------------------------------------------------------------
_jobs: Dict[str, Dict[str, Any]] = {}


def _make_state(session_id: str, file_path: str, file_type: str) -> Any:
    """Build a minimal valid AgentSwarmState for the demo."""
    from backend.core.state import AgentSwarmState, QAReport

    return AgentSwarmState(
        session_id=uuid.UUID(session_id),
        user_id="demo_user",
        pipeline_status="initiated",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        current_agent="system",
        raw_query="Clean this dataset",
        intent_class="trend_analysis",
        business_domain="unknown",
        key_entities=[],
        time_dimension=None,
        raw_file_path=file_path,
        file_type=file_type,
        raw_row_count=0,
        raw_col_count=0,
        schema_fingerprint="",
        schema_embedding_id=None,
        column_metadata=[],
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=1.0,
        rows_before=0,
        rows_after=0,
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


async def _emit(job_id: str, event: Dict[str, Any]):
    """Push one SSE event to the job's queue and persist it."""
    job = _jobs.get(job_id)
    if not job:
        return
    job["events"].append(event)
    await job["queue"].put(event)


async def _run_pipeline(job_id: str, file_path: str, file_type: str):
    """
    Background coroutine.  Runs ingestion_node then a PATCHED cleaning_node
    that emits SSE events per operation instead of bulk-writing at the end.
    """
    from backend.core.duckdb_engine import DuckDBEngine
    from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
    from backend.core.schema_profiler import SchemaProfiler
    from backend.core.schema_compressor import compress_column_meta_for_prompt
    from backend.agents.cleaning_node import _safe_cast_mixed_columns
    from backend.core.context_slicer import slice_context
    from backend.agents.prompts import CLEANING_SYSTEM_PROMPT
    from backend.core.state import CleaningOperation

    import re
    from datetime import datetime as dt

    db_engine = DuckDBEngine()
    llm_router = LLMRouter()

    try:
        # ------------------------------------------------------------------ #
        # PHASE 0 – Ingest the file
        # ------------------------------------------------------------------ #
        await _emit(job_id, {
            "type": "status",
            "message": "Loading file into DuckDB and profiling schema…",
            "phase": "ingestion"
        })

        load_result = db_engine.load_from_supabase(file_path, file_type)
        raw_row_count = load_result.get("row_count", 0)
        raw_col_count = load_result.get("column_count", 0)

        # Schema profiler
        schema_profiler = SchemaProfiler(db_engine)
        column_metadata = schema_profiler.profile_table()

        await _emit(job_id, {
            "type": "status",
            "message": f"Ingestion complete — {raw_col_count} columns, {raw_row_count} rows detected.",
            "phase": "ingestion"
        })

        # ------------------------------------------------------------------ #
        # PHASE 1 – LLM schema inference (needed for cleaning context)
        # ------------------------------------------------------------------ #
        await _emit(job_id, {
            "type": "status",
            "message": "Running LLM schema inference…",
            "phase": "schema_inference"
        })

        from backend.agents.prompts import INGESTION_SYSTEM_PROMPT
        compressed = compress_column_meta_for_prompt(column_metadata)
        user_msg = (
            f"Domain: unknown\n\nSchema Profile:\n{compressed}\n\n"
            f"Return a JSON object with a single key 'columns' containing "
            f"the array of ColumnMeta objects for ALL {raw_col_count} columns."
        )
        schema_resp = await llm_router.route(
            task_type=TaskType.SCHEMA_INFERENCE,
            messages=[
                {"role": "system", "content": INGESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4000
        )
        parsed_schema = parse_json_response(schema_resp["content"])
        inferred_cols: List[Dict] = []
        if isinstance(parsed_schema, list):
            inferred_cols = parsed_schema
        elif isinstance(parsed_schema, dict):
            inferred_cols = next(
                (v for v in parsed_schema.values() if isinstance(v, list)), []
            )
        inferred_map = {
            (c.get("name") or c.get("column_name")): c
            for c in inferred_cols if isinstance(c, dict)
        }
        col_meta_dicts = []
        for col in column_metadata:
            d = col.model_dump() if hasattr(col, "model_dump") else dict(col)
            inf = inferred_map.get(d["name"], {})
            d["semantic_type"] = inf.get("semantic_type", "unknown")
            d["business_label"] = inf.get("business_label", "")
            d["is_candidate_kpi"] = inf.get("is_candidate_kpi", False)
            col_meta_dicts.append(d)

        # ------------------------------------------------------------------ #
        # PHASE 2 – Data quality BEFORE
        # ------------------------------------------------------------------ #
        _safe_cast_mixed_columns(db_engine)
        lf = db_engine.to_polars_lazyframe()
        df_before = lf.collect()
        total_cells_before = df_before.height * df_before.width
        null_before = df_before.null_count().sum_horizontal().item()
        quality_before = round(
            max(0.0, 1.0 - (null_before / total_cells_before)) if total_cells_before else 1.0,
            4
        )
        await _emit(job_id, {
            "type": "quality_before",
            "score": quality_before,
            "score_pct": round(quality_before * 100, 1),
            "rows": df_before.height,
            "cols": df_before.width,
            "null_cells": null_before,
        })

        # ------------------------------------------------------------------ #
        # PHASE 3 – Cleaning strategy from LLM
        # ------------------------------------------------------------------ #
        await _emit(job_id, {
            "type": "status",
            "message": "Asking LLM for cleaning strategy…",
            "phase": "strategy"
        })

        duckdb_summary = db_engine.get_statistical_summary()
        clean_prompt = (
            f"Dataset Quality Profile:\n{duckdb_summary}\n\n"
            f"Column Metadata:\n{compress_column_meta_for_prompt(column_metadata)}\n\n"
            f"Business Intent:\ntopic_analysis\n\n"
            f"Business Domain:\nunknown\n\n"
            f"For each column requiring a cleaning action, return one CleaningOperation.\n\n"
            f"Only include columns that need changes.\n"
            f"Skip clean columns."
        )
        strategy_resp = await llm_router.route(
            task_type=TaskType.CLEANING_STRATEGY,
            messages=[
                {"role": "system", "content": CLEANING_SYSTEM_PROMPT},
                {"role": "user", "content": clean_prompt},
            ],
            max_tokens=4000
        )
        ops_raw = parse_json_response(strategy_resp["content"])
        operations_data = []
        if isinstance(ops_raw, list):
            operations_data = ops_raw
        elif isinstance(ops_raw, dict) and "operations" in ops_raw:
            operations_data = ops_raw["operations"]

        operations: List[CleaningOperation] = []
        for op in operations_data:
            if not isinstance(op, dict):
                continue
            op.setdefault("rows_affected", 0)
            op.setdefault("before_nulls", 0)
            op.setdefault("after_nulls", 0)
            op.setdefault("polars_code", "")
            try:
                operations.append(CleaningOperation(**op))
            except Exception:
                pass

        await _emit(job_id, {
            "type": "status",
            "message": f"Strategy generated — {len(operations)} operations planned.",
            "phase": "strategy"
        })

        # ------------------------------------------------------------------ #
        # PHASE 4 – Execute each operation and emit per-op SSE events
        # ------------------------------------------------------------------ #
        SAFE_GLOBALS = {
            "__builtins__": {},
            "pl": pl,
            "re": re,
            "datetime": dt,
        }
        columns_dropped: List[str] = []

        OPERATION_ICONS = {
            "fill_null": "wand",
            "cast_type": "wrench",
            "trim_whitespace": "scissors",
            "normalize": "wand",
            "drop_column": "trash",
            "deduplicate": "copy",
            "remove_outlier": "filter",
            "parse_date": "calendar",
        }

        for idx, op in enumerate(operations):
            polars_code = ""

            if op.operation == "drop_column":
                lf = lf.drop(op.column)
                columns_dropped.append(op.column)
                await _emit(job_id, {
                    "type": "operation",
                    "index": idx,
                    "column": op.column,
                    "operation": op.operation,
                    "strategy": op.strategy,
                    "rows_affected": 0,
                    "rationale": op.rationale,
                    "icon": "trash",
                    "polars_code": f"lf = lf.drop('{op.column}')",
                })
                continue

            code_prompt = (
                f"Generate a Polars expression for a Polars LazyFrame called `lf`.\n"
                f"Operation: {op.operation}. Column: '{op.column}'. Strategy: {op.strategy}.\n"
                f"The expression will be used as: `lf = lf.with_columns([YOUR_EXPRESSION])`\n\n"
                f"Strategy reference:\n"
                f"- safe_numeric_cast: pl.col('{op.column}').str.extract(r'([-+]?\\d+\\.?\\d*)', 0).cast(pl.Float64, strict=False)\n"
                f"- normalize_categorical: pl.col('{op.column}').str.to_lowercase().str.strip_chars()\n"
                f"- strip: pl.col('{op.column}').str.strip_chars()\n"
                f"- null_invalid_email: pl.when(pl.col('{op.column}').str.contains('@')).then(pl.col('{op.column}')).otherwise(pl.lit(None))\n"
                f"- median: pl.col('{op.column}').fill_null(pl.col('{op.column}').median())\n"
                f"- mode: pl.col('{op.column}').fill_null(pl.col('{op.column}').mode().first())\n\n"
                f"Return ONLY the Python expression. No markdown, no imports."
            )

            success = False
            for attempt in range(3):
                try:
                    code_resp = await llm_router.route(
                        task_type=TaskType.CODE_GENERATION,
                        messages=[
                            {"role": "system", "content": "You are a Polars expert. Return ONLY valid Python code. No markdown."},
                            {"role": "user", "content": code_prompt},
                        ],
                        max_tokens=500
                    )
                    polars_code = code_resp["content"].strip()
                    if polars_code.startswith("```"):
                        polars_code = re.sub(r"```(?:python)?", "", polars_code).strip("`").strip()

                    SAFE_LOCALS = {"lf": lf}
                    exec(f"lf = lf.with_columns([{polars_code}])", SAFE_GLOBALS, SAFE_LOCALS)
                    lf = SAFE_LOCALS["lf"]
                    success = True
                    break
                except Exception as e:
                    code_prompt = (
                        f"That expression failed with error: {e}. Fix it. "
                        f"Previous code: {polars_code}. Return ONLY the Python expression."
                    )

            if success:
                await _emit(job_id, {
                    "type": "operation",
                    "index": idx,
                    "column": op.column,
                    "operation": op.operation,
                    "strategy": op.strategy,
                    "rows_affected": op.rows_affected,
                    "rationale": op.rationale,
                    "icon": OPERATION_ICONS.get(op.operation, "wrench"),
                    "polars_code": polars_code,
                })
            else:
                await _emit(job_id, {
                    "type": "operation_skipped",
                    "index": idx,
                    "column": op.column,
                    "operation": op.operation,
                    "reason": "Code generation failed after 3 attempts",
                })

        # ------------------------------------------------------------------ #
        # PHASE 5 – Quality AFTER + preview
        # ------------------------------------------------------------------ #
        df_after = lf.collect()
        total_cells_after = df_after.height * df_after.width
        null_after = df_after.null_count().sum_horizontal().item()
        quality_after = round(
            max(0.0, 1.0 - (null_after / total_cells_after)) if total_cells_after else 1.0,
            4
        )

        await _emit(job_id, {
            "type": "quality_after",
            "score": quality_after,
            "score_pct": round(quality_after * 100, 1),
            "rows_after": df_after.height,
            "cols_after": df_after.width,
            "null_cells": null_after,
            "improvement": round((quality_after - quality_before) * 100, 2),
            "columns_dropped": columns_dropped,
        })

        # Preview: first 20 rows, stringify everything for JSON safety
        preview_df = df_after.head(20)
        preview_rows = [
            {col: (str(val) if val is not None else None) for col, val in zip(preview_df.columns, row)}
            for row in preview_df.rows()
        ]
        await _emit(job_id, {
            "type": "preview",
            "columns": df_after.columns,
            "rows": preview_rows,
        })

        # Store cleaned CSV bytes for download
        csv_buf = io.StringIO()
        df_after.write_csv(csv_buf)
        _jobs[job_id]["cleaned_csv_bytes"] = csv_buf.getvalue().encode("utf-8")
        _jobs[job_id]["status"] = "done"

        await _emit(job_id, {"type": "done"})

    except Exception as exc:
        logger.error(f"Cleaning demo pipeline failed for job {job_id}: {exc}")
        logger.error(traceback.format_exc())
        _jobs[job_id]["status"] = "error"
        await _emit(job_id, {"type": "error", "message": str(exc)})


# ---------------------------------------------------------------------------
# POST /api/cleaning/run
# ---------------------------------------------------------------------------
@router.post("/run")
async def run_cleaning(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Accept a CSV or XLSX file, save it locally, and start the cleaning pipeline.
    Returns { job_id } immediately; client then opens the /stream SSE endpoint.
    """
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "csv"
    if ext not in ("csv", "xlsx", "json", "parquet"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    job_id = str(uuid.uuid4())
    save_dir = os.path.join("tmp_storage", "demo", job_id)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"input.{ext}")

    with open(save_path, "wb") as f:
        f.write(file_bytes)

    _jobs[job_id] = {
        "status": "running",
        "queue": asyncio.Queue(),
        "events": [],
        "cleaned_csv_bytes": None,
        "file_path": save_path,
        "file_type": ext,
        "original_filename": filename,
    }

    background_tasks.add_task(_run_pipeline, job_id, save_path, ext)

    return {"job_id": job_id, "file_type": ext, "filename": filename}


# ---------------------------------------------------------------------------
# GET /api/cleaning/{job_id}/stream  — SSE
# ---------------------------------------------------------------------------
@router.get("/{job_id}/stream")
async def stream_cleaning(job_id: str):
    """SSE endpoint: emits all queued events then keeps streaming until done/error."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_generator():
        # Replay past events so late-joiners (page refresh) see full history
        for past_event in list(job["events"]):
            yield f"data: {json.dumps(past_event)}\n\n"
            if past_event.get("type") in ("done", "error"):
                return

        # Then stream new events from the queue
        q: asyncio.Queue = job["queue"]
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=120.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"heartbeat\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        }
    )


# ---------------------------------------------------------------------------
# GET /api/cleaning/{job_id}/download  — Download cleaned CSV
# ---------------------------------------------------------------------------
@router.get("/{job_id}/download")
async def download_cleaned(job_id: str):
    """Return the cleaned dataset as a downloadable CSV file."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail="Cleaning pipeline not yet complete.")
    csv_bytes = job.get("cleaned_csv_bytes")
    if not csv_bytes:
        raise HTTPException(status_code=500, detail="Cleaned data not available.")

    original = job.get("original_filename", "dataset")
    stem = original.rsplit(".", 1)[0] if "." in original else original
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem}_cleaned.csv"'},
    )
