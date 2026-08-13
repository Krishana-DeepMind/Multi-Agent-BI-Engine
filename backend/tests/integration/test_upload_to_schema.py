"""
Days 6-7 Integration Test: Upload to Schema Display
=====================================================
Blueprint Spec (Lines 915-922):
  - End-to-end: Upload 3 CSV files → DuckDB load → Router node → Schema profile → Displayed in UI
  - Check: file type detection, Supabase Storage auth, DuckDB memory limits, SSE connection drops
  - Performance: under 20 seconds from upload to schema visible
  - Document all bugs and fixes

This test suite exercises the entire Sprint 1 pipeline without requiring
real Supabase credentials (the SupabaseService auto-falls back to mock mode).
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import io
import csv
import json
import time
import uuid
import asyncio
import tempfile
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, field

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("integration_test")


# ─── Test CSV Data Generators ─────────────────────────────────────────────────

def generate_clean_sales_csv() -> bytes:
    """CSV 1: Clean sales data — standard tabular, no quirks."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["order_id", "date", "product", "region", "quantity", "unit_price", "revenue"])
    import random
    random.seed(42)
    products = ["Widget A", "Widget B", "Gizmo", "Sprocket", "Bolt"]
    regions = ["North", "South", "East", "West"]
    for i in range(500):
        order_id = f"ORD-{1000 + i}"
        date = f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        product = random.choice(products)
        region = random.choice(regions)
        qty = random.randint(1, 100)
        price = round(random.uniform(5.0, 200.0), 2)
        rev = round(qty * price, 2)
        writer.writerow([order_id, date, product, region, qty, price, rev])
    return buf.getvalue().encode("utf-8")


def generate_messy_hr_csv() -> bytes:
    """CSV 2: Messy HR data — mixed types, nulls, inconsistent formatting."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["emp_id", "name", "department", "salary", "hire_date", "is_active", "rating"])
    import random
    random.seed(123)
    departments = ["Engineering", "Sales", "HR", "Marketing", "Operations"]
    names = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi",
             "Ivan", "Judy", "Karl", "Laura", "Mike", "Nancy", "Oscar"]
    for i in range(300):
        emp_id = f"EMP{i:04d}"
        name = random.choice(names) + f" {chr(65 + random.randint(0, 25))}"
        dept = random.choice(departments)
        # Introduce nulls (~10% chance)
        salary = "" if random.random() < 0.10 else str(round(random.uniform(35000, 150000), 2))
        hire_date = "" if random.random() < 0.05 else f"20{random.randint(15, 24)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        is_active = random.choice(["true", "false", "True", "FALSE", "yes", "1", ""])
        rating = "" if random.random() < 0.08 else str(round(random.uniform(1.0, 5.0), 1))
        writer.writerow([emp_id, name, dept, salary, hire_date, is_active, rating])
    return buf.getvalue().encode("utf-8")


def generate_financial_csv() -> bytes:
    """CSV 3: Financial time-series — dates, currency, percentages."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "ticker", "open", "close", "high", "low", "volume", "change_pct"])
    import random
    random.seed(999)
    tickers = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
    base_prices = {"AAPL": 175.0, "GOOGL": 140.0, "MSFT": 350.0, "AMZN": 180.0, "TSLA": 250.0}
    for day in range(1, 61):  # 60 trading days
        for ticker in tickers:
            date = f"2024-{((day - 1) // 28) + 1:02d}-{((day - 1) % 28) + 1:02d}"
            base = base_prices[ticker]
            open_p = round(base + random.uniform(-5, 5), 2)
            close_p = round(open_p + random.uniform(-3, 3), 2)
            high_p = round(max(open_p, close_p) + random.uniform(0, 2), 2)
            low_p = round(min(open_p, close_p) - random.uniform(0, 2), 2)
            volume = random.randint(1_000_000, 50_000_000)
            change = round(((close_p - open_p) / open_p) * 100, 4)
            writer.writerow([date, ticker, open_p, close_p, high_p, low_p, volume, change])
            base_prices[ticker] = close_p  # random walk
    return buf.getvalue().encode("utf-8")


# ─── Result Container ─────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    bugs: List[str] = field(default_factory=list)
    error: str = ""


# ─── Integration Test Functions ────────────────────────────────────────────────

def test_file_type_detection(csv_bytes: bytes, expected_type: str, label: str) -> TestResult:
    """Test 1: Validate magic-byte file type detection for CSV."""
    from backend.core.file_validator import detect_file_type_magic, estimate_row_count

    start = time.perf_counter()
    bugs = []
    try:
        detected = detect_file_type_magic(csv_bytes, f"{label}.csv")
        row_est = estimate_row_count(csv_bytes, detected)
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        if detected != expected_type:
            bugs.append(f"[BUG] File type detection: expected '{expected_type}', got '{detected}' for {label}")

        return TestResult(
            name=f"FileTypeDetection:{label}",
            passed=detected == expected_type,
            duration_ms=elapsed,
            details={"detected_type": detected, "row_estimate": row_est, "file_size_bytes": len(csv_bytes)},
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"FileTypeDetection:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] File type detection threw exception for {label}: {e}"],
        )


def test_duckdb_load(csv_bytes: bytes, label: str) -> TestResult:
    """Test 2: Load CSV bytes into DuckDB, verify schema profile and statistical summary."""
    from backend.core.duckdb_engine import DuckDBEngine

    start = time.perf_counter()
    bugs = []
    engine = DuckDBEngine()
    try:
        result = engine.load_from_bytes(csv_bytes, "csv", table_name=f"test_{label}")
        elapsed_load = round((time.perf_counter() - start) * 1000, 2)

        if not result.get("success"):
            bugs.append(f"[BUG] DuckDB load failed for {label}: {result}")
            return TestResult(name=f"DuckDBLoad:{label}", passed=False, duration_ms=elapsed_load, bugs=bugs, error="Load returned success=False")

        # Test schema profile
        try:
            schema_profile = engine.get_schema_profile(f"test_{label}")
            if not schema_profile or len(schema_profile) == 0:
                bugs.append(f"[BUG] Schema profile is empty for {label}")
        except Exception as e:
            bugs.append(f"[BUG] Schema profile failed for {label}: {e}")

        # Test statistical summary
        try:
            stats = engine.get_statistical_summary(f"test_{label}")
            if not stats or len(stats) == 0:
                bugs.append(f"[BUG] Statistical summary is empty for {label}")
        except Exception as e:
            bugs.append(f"[BUG] Statistical summary failed for {label}: {e}")

        # Test execute_validated
        try:
            query_result = engine.execute_validated(f"SELECT COUNT(*) as cnt FROM test_{label}")
            if not query_result.get("success"):
                bugs.append(f"[BUG] execute_validated failed for {label}: {query_result.get('error')}")
        except Exception as e:
            bugs.append(f"[BUG] execute_validated threw exception for {label}: {e}")

        # Test write_to_parquet
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                parquet_path = os.path.join(tmpdir, f"test_{label}.parquet")
                engine.write_to_parquet(parquet_path, f"test_{label}")
                if not os.path.exists(parquet_path):
                    bugs.append(f"[BUG] Parquet output file not created for {label}")
                elif os.path.getsize(parquet_path) == 0:
                    bugs.append(f"[BUG] Parquet output file is empty for {label}")
        except Exception as e:
            bugs.append(f"[BUG] write_to_parquet failed for {label}: {e}")

        elapsed_total = round((time.perf_counter() - start) * 1000, 2)

        return TestResult(
            name=f"DuckDBLoad:{label}",
            passed=len(bugs) == 0,
            duration_ms=elapsed_total,
            details={
                "row_count": result.get("row_count"),
                "column_count": result.get("column_count"),
                "columns": result.get("columns"),
                "load_ms": elapsed_load,
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"DuckDBLoad:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] DuckDB load threw exception for {label}: {e}"],
        )
    finally:
        engine.close()


def test_schema_profiler(csv_bytes: bytes, label: str) -> TestResult:
    """Test 3: Run SchemaProfiler and validate ColumnMeta output."""
    from backend.core.duckdb_engine import DuckDBEngine
    from backend.core.schema_profiler import SchemaProfiler

    start = time.perf_counter()
    bugs = []
    engine = DuckDBEngine()
    try:
        engine.load_from_bytes(csv_bytes, "csv", table_name=f"prof_{label}")
        profiler = SchemaProfiler(engine)
        column_metas = profiler.profile_table(f"prof_{label}")
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        if not column_metas:
            bugs.append(f"[BUG] SchemaProfiler returned empty metadata for {label}")
            return TestResult(name=f"SchemaProfiler:{label}", passed=False, duration_ms=elapsed, bugs=bugs)

        # Validate ColumnMeta fields
        for meta in column_metas:
            if meta.null_pct < 0.0 or meta.null_pct > 1.0:
                bugs.append(f"[BUG] null_pct out of range for column '{meta.name}': {meta.null_pct}")
            if meta.unique_pct < 0.0 or meta.unique_pct > 1.0:
                bugs.append(f"[BUG] unique_pct out of range for column '{meta.name}': {meta.unique_pct}")
            if len(meta.sample_values) > 5:
                bugs.append(f"[BUG] sample_values exceeds max 5 for column '{meta.name}': {len(meta.sample_values)}")
            if not meta.name:
                bugs.append(f"[BUG] Empty column name in ColumnMeta for {label}")
            if not meta.dtype:
                bugs.append(f"[BUG] Empty dtype in ColumnMeta for column '{meta.name}' in {label}")

        return TestResult(
            name=f"SchemaProfiler:{label}",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details={
                "column_count": len(column_metas),
                "columns": [{"name": m.name, "dtype": m.dtype, "null_pct": m.null_pct, "unique_pct": m.unique_pct} for m in column_metas],
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"SchemaProfiler:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] SchemaProfiler threw exception for {label}: {e}"],
        )
    finally:
        engine.close()


def test_schema_compressor(csv_bytes: bytes, label: str) -> TestResult:
    """Test 4: Validate SchemaCompressor produces compact prompt-friendly output."""
    from backend.core.duckdb_engine import DuckDBEngine
    from backend.core.schema_profiler import SchemaProfiler
    from backend.core.schema_compressor import compress_column_meta_for_prompt

    start = time.perf_counter()
    bugs = []
    engine = DuckDBEngine()
    try:
        engine.load_from_bytes(csv_bytes, "csv", table_name=f"comp_{label}")
        profiler = SchemaProfiler(engine)
        metas = profiler.profile_table(f"comp_{label}")

        # Convert Pydantic models to dicts for the compressor
        meta_dicts = [m.model_dump() for m in metas]
        compressed = compress_column_meta_for_prompt(metas)
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        if not compressed or len(compressed.strip()) == 0:
            bugs.append(f"[BUG] SchemaCompressor returned empty output for {label}")

        # Verify it's a table format with pipes
        if "|" not in compressed:
            bugs.append(f"[BUG] SchemaCompressor output lacks table format (no '|') for {label}")

        # Verify token efficiency: compressed should be significantly shorter than raw JSON
        raw_json = json.dumps(meta_dicts)
        compression_ratio = len(compressed) / len(raw_json) if len(raw_json) > 0 else 1.0
        if compression_ratio > 0.5:
            bugs.append(f"[BUG] SchemaCompressor ratio too high ({compression_ratio:.2f}) — not compressing well for {label}")

        return TestResult(
            name=f"SchemaCompressor:{label}",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details={
                "compressed_length": len(compressed),
                "raw_json_length": len(raw_json),
                "compression_ratio": round(compression_ratio, 4),
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"SchemaCompressor:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] SchemaCompressor threw exception for {label}: {e}"],
        )
    finally:
        engine.close()


def test_context_slicer() -> TestResult:
    """Test 5: Validate ContextSlicer returns only agent-relevant fields."""
    from backend.core.context_slicer import slice_context, AGENT_CONTEXT_FIELDS
    from backend.core.state import AgentSwarmState, QAReport

    start = time.perf_counter()
    bugs = []
    try:
        # Build a minimal mock state dict
        mock_state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test-user",
            pipeline_status="initiated",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            current_agent="system",
            raw_query="Show me revenue trends last quarter",
            intent_class="trend_analysis",
            business_domain="finance",
            key_entities=["revenue"],
            time_dimension="last quarter",
            raw_file_path="test/path.csv",
            file_type="csv",
            raw_row_count=500,
            raw_col_count=7,
            schema_fingerprint="",
            schema_embedding_id=None,
            column_metadata=[],
            similar_schemas_found=False,
            cleaning_operations=[],
            cleaned_parquet_path="",
            data_quality_score=1.0,
            rows_before=500,
            rows_after=500,
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

        mock_state_dict = mock_state.model_dump()
        for agent_name, expected_fields in AGENT_CONTEXT_FIELDS.items():
            sliced = slice_context(mock_state, agent_name)

            if expected_fields == ["*"]:
                # QA agent sees full state — sliced should have at least as many keys as mock_state
                if len(sliced) < len(mock_state_dict):
                    bugs.append(f"[BUG] QA agent context missing fields: got {len(sliced)}, expected {len(mock_state_dict)}")
            else:
                # Check that only expected fields are present
                for f in expected_fields:
                    if f in mock_state_dict and f not in sliced:
                        bugs.append(f"[BUG] ContextSlicer missing field '{f}' for agent '{agent_name}'")
                # Check no extra fields leaked
                for f in sliced:
                    if f not in expected_fields:
                        bugs.append(f"[BUG] ContextSlicer leaked field '{f}' to agent '{agent_name}'")

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name="ContextSlicer",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details={"agents_tested": list(AGENT_CONTEXT_FIELDS.keys())},
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name="ContextSlicer",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] ContextSlicer threw exception: {e}"],
        )


async def test_router_node(csv_bytes: bytes, label: str, raw_query: str) -> TestResult:
    """Test 6: Run the Router Node and validate classification output."""
    from backend.core.state import AgentSwarmState, QAReport
    from backend.agents.router_node import router_node
    from backend.core.duckdb_engine import DuckDBEngine

    start = time.perf_counter()
    bugs = []

    engine = DuckDBEngine()
    try:
        load_result = engine.load_from_bytes(csv_bytes, "csv", table_name=f"router_{label}")

        initial_state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test-user",
            pipeline_status="initiated",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            current_agent="system",
            raw_query=raw_query,
            intent_class="trend_analysis",
            business_domain="unknown",
            key_entities=[],
            time_dimension=None,
            raw_file_path=f"test/{label}.csv",
            file_type="csv",
            raw_row_count=load_result.get("row_count", 0),
            raw_col_count=load_result.get("column_count", 0),
            schema_fingerprint="",
            schema_embedding_id=None,
            column_metadata=[],
            similar_schemas_found=False,
            cleaning_operations=[],
            cleaned_parquet_path="",
            data_quality_score=1.0,
            rows_before=load_result.get("row_count", 0),
            rows_after=load_result.get("row_count", 0),
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
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        # Validate router output
        valid_intents = ["trend_analysis", "root_cause", "comparison", "distribution", "correlation", "ranking", "forecasting"]
        if routed_state.intent_class not in valid_intents:
            bugs.append(f"[BUG] Router returned invalid intent '{routed_state.intent_class}' for query: {raw_query}")

        valid_domains = ["finance", "sales", "operations", "marketing", "hr", "ecommerce", "iot",
                         "customer_success", "supply_chain", "logistics", "healthcare", "education", "unknown"]
        if routed_state.business_domain not in valid_domains:
            bugs.append(f"[BUG] Router returned invalid domain '{routed_state.business_domain}' for query: {raw_query}")

        if routed_state.pipeline_status != "routing":
            bugs.append(f"[BUG] Router did not set pipeline_status to 'routing': got '{routed_state.pipeline_status}'")

        if routed_state.current_agent != "router":
            bugs.append(f"[BUG] Router did not set current_agent to 'router': got '{routed_state.current_agent}'")

        return TestResult(
            name=f"RouterNode:{label}",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details={
                "intent_class": routed_state.intent_class,
                "business_domain": routed_state.business_domain,
                "key_entities": routed_state.key_entities,
                "time_dimension": routed_state.time_dimension,
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"RouterNode:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] RouterNode threw exception for {label}: {e}"],
        )
    finally:
        engine.close()


async def test_upload_api(csv_bytes: bytes, label: str) -> TestResult:
    """Test 7: Test the FastAPI upload endpoint via TestClient."""
    start = time.perf_counter()
    bugs = []
    try:
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/upload",
            files={"file": (f"{label}.csv", csv_bytes, "text/csv")},
            data={"user_id": "00000000-0000-0000-0000-000000000000"},
        )
        elapsed = round((time.perf_counter() - start) * 1000, 2)

        if response.status_code != 201:
            bugs.append(f"[BUG] Upload API returned status {response.status_code} for {label}: {response.text}")
            return TestResult(
                name=f"UploadAPI:{label}",
                passed=False,
                duration_ms=elapsed,
                error=f"Status {response.status_code}: {response.text}",
                bugs=bugs,
            )

        data = response.json()
        required_fields = ["session_id", "file_path", "file_type", "file_size_mb", "row_count_estimate"]
        for rf in required_fields:
            if rf not in data:
                bugs.append(f"[BUG] Upload API response missing field '{rf}' for {label}")

        if data.get("file_type") != "csv":
            bugs.append(f"[BUG] Upload API detected wrong file type '{data.get('file_type')}' for {label}")

        if data.get("row_count_estimate", 0) <= 0:
            bugs.append(f"[BUG] Upload API returned invalid row_count_estimate {data.get('row_count_estimate')} for {label}")

        return TestResult(
            name=f"UploadAPI:{label}",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details=data,
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"UploadAPI:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] Upload API threw exception for {label}: {e}"],
        )


async def test_pipeline_sse(session_id: str) -> TestResult:
    """Test 8: Start pipeline and verify SSE events stream correctly."""
    start = time.perf_counter()
    bugs = []
    try:
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)

        # Start the pipeline
        start_resp = client.post(f"/api/pipeline/{session_id}/start?raw_query=Show me sales trends")
        if start_resp.status_code != 200:
            bugs.append(f"[BUG] Pipeline start returned status {start_resp.status_code}: {start_resp.text}")
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            return TestResult(name="PipelineSSE", passed=False, duration_ms=elapsed, bugs=bugs)

        # Read SSE events
        events = []
        with client.stream("GET", f"/api/pipeline/{session_id}/stream") as sse_resp:
            if sse_resp.status_code != 200:
                bugs.append(f"[BUG] SSE stream returned status {sse_resp.status_code}")
            else:
                for line in sse_resp.iter_lines():
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            events.append(event)
                        except json.JSONDecodeError:
                            bugs.append(f"[BUG] SSE event is not valid JSON: {line}")

        elapsed = round((time.perf_counter() - start) * 1000, 2)

        if len(events) == 0:
            bugs.append("[BUG] No SSE events received from pipeline stream")
        else:
            # Check that we got a 'complete' or 'failed' terminal event
            statuses = [e.get("status") for e in events]
            if "complete" not in statuses and "failed" not in statuses:
                bugs.append(f"[BUG] Pipeline did not reach terminal state. Statuses: {statuses}")

        return TestResult(
            name="PipelineSSE",
            passed=len(bugs) == 0,
            duration_ms=elapsed,
            details={
                "event_count": len(events),
                "statuses": [e.get("status") for e in events],
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name="PipelineSSE",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] PipelineSSE threw exception: {e}"],
        )


async def test_end_to_end_performance(csv_bytes: bytes, label: str, query: str) -> TestResult:
    """
    Test 9: Full end-to-end performance benchmark.
    Upload → DuckDB Load → Schema Profile → Router Node → Schema Compress
    Must complete under 20 seconds.
    """
    start = time.perf_counter()
    bugs = []
    timings = {}

    try:
        from backend.core.file_validator import detect_file_type_magic, estimate_row_count
        from backend.core.duckdb_engine import DuckDBEngine
        from backend.core.schema_profiler import SchemaProfiler
        from backend.core.schema_compressor import compress_column_meta_for_prompt
        from backend.core.state import AgentSwarmState, QAReport
        from backend.agents.router_node import router_node

        # Step 1: File detection
        t0 = time.perf_counter()
        file_type = detect_file_type_magic(csv_bytes, f"{label}.csv")
        row_est = estimate_row_count(csv_bytes, file_type)
        timings["file_detection_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Step 2: DuckDB load
        t0 = time.perf_counter()
        engine = DuckDBEngine()
        load_res = engine.load_from_bytes(csv_bytes, file_type, table_name=f"e2e_{label}")
        timings["duckdb_load_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Step 3: Schema profiling
        t0 = time.perf_counter()
        profiler = SchemaProfiler(engine)
        metas = profiler.profile_table(f"e2e_{label}")
        timings["schema_profile_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Step 4: Schema compression
        t0 = time.perf_counter()
        meta_dicts = [m.model_dump() for m in metas]
        compressed = compress_column_meta_for_prompt(metas)
        timings["schema_compress_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Step 5: Router node
        t0 = time.perf_counter()
        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="perf-test",
            pipeline_status="initiated",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            current_agent="system",
            raw_query=query,
            intent_class="trend_analysis",
            business_domain="unknown",
            key_entities=[],
            time_dimension=None,
            raw_file_path=f"test/{label}.csv",
            file_type="csv",
            raw_row_count=load_res.get("row_count", 0),
            raw_col_count=load_res.get("column_count", 0),
            schema_fingerprint="",
            schema_embedding_id=None,
            column_metadata=metas,
            similar_schemas_found=False,
            cleaning_operations=[],
            cleaned_parquet_path="",
            data_quality_score=1.0,
            rows_before=load_res.get("row_count", 0),
            rows_after=load_res.get("row_count", 0),
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
        routed = await router_node(state)
        timings["router_node_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        total_ms = round((time.perf_counter() - start) * 1000, 2)
        timings["total_ms"] = total_ms

        engine.close()

        # Performance check: must be under 20 seconds
        if total_ms > 20000:
            bugs.append(f"[BUG] End-to-end took {total_ms}ms — exceeds 20s target for {label}")

        return TestResult(
            name=f"E2EPerformance:{label}",
            passed=len(bugs) == 0 and total_ms <= 20000,
            duration_ms=total_ms,
            details={
                "timings": timings,
                "row_count": load_res.get("row_count"),
                "column_count": load_res.get("column_count"),
                "intent": routed.intent_class,
                "domain": routed.business_domain,
            },
            bugs=bugs,
        )
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return TestResult(
            name=f"E2EPerformance:{label}",
            passed=False,
            duration_ms=elapsed,
            error=str(e),
            bugs=[f"[BUG] E2E performance test threw exception for {label}: {e}"],
        )


# ─── Main Test Runner ──────────────────────────────────────────────────────────

async def run_all_tests():
    """Execute all Days 6-7 integration tests and produce a report."""
    print("\n" + "=" * 80)
    print("  DAYS 6-7 INTEGRATION TEST: Upload to Schema Display")
    print("  Blueprint Ref: Lines 915-922")
    print("=" * 80 + "\n")

    # Generate test CSVs
    print("Generating 3 test CSV datasets...")
    csvs = {
        "clean_sales": {
            "bytes": generate_clean_sales_csv(),
            "query": "Show me why sales dropped in Q3 2024",
        },
        "messy_hr": {
            "bytes": generate_messy_hr_csv(),
            "query": "What is the attrition rate by department and why are employees leaving?",
        },
        "financial_ts": {
            "bytes": generate_financial_csv(),
            "query": "Compare stock performance of AAPL vs GOOGL last quarter",
        },
    }

    for name, data in csvs.items():
        sz = len(data["bytes"])
        print(f"  [PASS] {name}: {sz:,} bytes ({sz / 1024:.1f} KB)")

    results: List[TestResult] = []

    # ─── Test Suite 1: File Type Detection ──────────────────────────────────
    print("\n── Test Suite 1: File Type Detection ──")
    for label, data in csvs.items():
        r = test_file_type_detection(data["bytes"], "csv", label)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {icon} {r.name} ({r.duration_ms}ms)")

    # ─── Test Suite 2: DuckDB Load & Profile ───────────────────────────────
    print("\n── Test Suite 2: DuckDB Load & Profile ──")
    for label, data in csvs.items():
        r = test_duckdb_load(data["bytes"], label)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {icon} {r.name} ({r.duration_ms}ms) — {r.details.get('row_count', '?')} rows, {r.details.get('column_count', '?')} cols")

    # ─── Test Suite 3: Schema Profiler ──────────────────────────────────────
    print("\n── Test Suite 3: Schema Profiler ──")
    for label, data in csvs.items():
        r = test_schema_profiler(data["bytes"], label)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {icon} {r.name} ({r.duration_ms}ms) — {r.details.get('column_count', '?')} columns profiled")

    # ─── Test Suite 4: Schema Compressor ────────────────────────────────────
    print("\n── Test Suite 4: Schema Compressor ──")
    for label, data in csvs.items():
        r = test_schema_compressor(data["bytes"], label)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        ratio = r.details.get("compression_ratio", "?")
        print(f"  {icon} {r.name} ({r.duration_ms}ms) — compression ratio: {ratio}")

    # ─── Test Suite 5: Context Slicer ───────────────────────────────────────
    print("\n── Test Suite 5: Context Slicer ──")
    r = test_context_slicer()
    results.append(r)
    icon = "✓" if r.passed else "✗"
    print(f"  {icon} {r.name} ({r.duration_ms}ms)")

    # ─── Test Suite 6: Router Node ──────────────────────────────────────────
    print("\n── Test Suite 6: Router Node ──")
    for label, data in csvs.items():
        r = await test_router_node(data["bytes"], label, data["query"])
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {icon} {r.name} ({r.duration_ms}ms) — intent: {r.details.get('intent_class')}, domain: {r.details.get('business_domain')}")

    # ─── Test Suite 7: Upload API ───────────────────────────────────────────
    print("\n── Test Suite 7: Upload API ──")
    for label, data in csvs.items():
        r = await test_upload_api(data["bytes"], label)
        results.append(r)
        icon = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {icon} {r.name} ({r.duration_ms}ms)")

    # ─── Test Suite 8: Pipeline SSE ─────────────────────────────────────────
    print("\n── Test Suite 8: Pipeline SSE ──")
    test_session_id = str(uuid.uuid4())
    r = await test_pipeline_sse(test_session_id)
    results.append(r)
    icon = "✓" if r.passed else "✗"
    print(f"  {icon} {r.name} ({r.duration_ms}ms) — events: {r.details.get('event_count', 0)}")

    # ─── Test Suite 9: End-to-End Performance ───────────────────────────────
    print("\n── Test Suite 9: End-to-End Performance (target: <20s) ──")
    for label, data in csvs.items():
        r = await test_end_to_end_performance(data["bytes"], label, data["query"])
        results.append(r)
        icon = "✓" if r.passed else "✗"
        timings = r.details.get("timings", {})
        print(f"  {icon} {r.name} ({r.duration_ms}ms total)")
        for k, v in timings.items():
            if k != "total_ms":
                print(f"      {k}: {v}ms")

    # ─── Summary Report ────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  RESULTS SUMMARY")
    print("=" * 80)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    print(f"\n  Total: {total} | Passed: {passed} | Failed: {failed}\n")

    all_bugs = []
    for r in results:
        if r.bugs:
            all_bugs.extend(r.bugs)
        if r.error:
            all_bugs.append(f"[ERROR] {r.name}: {r.error}")

    if all_bugs:
        print("  ── Bugs Found ──")
        for bug in all_bugs:
            print(f"    • {bug}")
    else:
        print("  [PASS] No bugs found!")

    # Print performance summary
    print("\n  ── Performance Summary ──")
    for r in results:
        if r.name.startswith("E2EPerformance"):
            status = "PASS" if r.duration_ms <= 20000 else "FAIL"
            print(f"    {r.name}: {r.duration_ms}ms [{status}]")

    print("\n" + "=" * 80)
    print(f"  OVERALL: {'ALL TESTS PASSED [PASS]' if failed == 0 else f'{failed} TEST(S) FAILED [FAIL]'}")
    print("=" * 80 + "\n")

    return results, all_bugs


if __name__ == "__main__":
    results, bugs = asyncio.run(run_all_tests())
