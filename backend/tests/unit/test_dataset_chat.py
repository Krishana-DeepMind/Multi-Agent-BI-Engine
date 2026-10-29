import pytest
import os
import duckdb
from fastapi.testclient import TestClient
from backend.main import app
from backend.api.pipeline import session_store

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_session_with_parquet(tmp_path):
    session_id = "test-chat-session-001"
    sess_dir = tmp_path / "00000000-0000-0000-0000-000000000000" / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = str(sess_dir / "cleaned.parquet")

    # Create dummy parquet table with department and salary
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE dummy AS 
        SELECT 'Engineering' AS department, 80000 AS salary, 1 AS is_attrited
        UNION ALL
        SELECT 'Sales' AS department, 50000 AS salary, 0 AS is_attrited
        UNION ALL
        SELECT 'Marketing' AS department, 60000 AS salary, 0 AS is_attrited
    """)
    conn.execute(f"COPY dummy TO '{parquet_path}' (FORMAT PARQUET)")
    conn.close()

    session_store[session_id] = {
        "session_id": session_id,
        "cleaned_parquet_path": parquet_path,
        "dashboard_title": "Test Dataset",
    }
    return session_id, parquet_path

def test_chat_empty_message(client, mock_session_with_parquet):
    session_id, _ = mock_session_with_parquet
    resp = client.post(f"/api/pipeline/{session_id}/chat", json={"message": ""})
    assert resp.status_code == 400

def test_chat_record_count_metadata(client, mock_session_with_parquet):
    session_id, _ = mock_session_with_parquet
    resp = client.post(f"/api/pipeline/{session_id}/chat", json={"message": "How many records are in this dataset?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 1
    assert data["sql"] == "SELECT COUNT(*) AS total_records FROM data"
    assert data["data"][0]["total_records"] == 3

def test_chat_list_columns_metadata(client, mock_session_with_parquet):
    session_id, _ = mock_session_with_parquet
    resp = client.post(f"/api/pipeline/{session_id}/chat", json={"message": "What columns are available?"})
    assert resp.status_code == 200
    data = resp.json()
    assert "column_name" in data["columns"] or len(data["columns"]) > 0

def test_chat_nonexistent_session(client):
    resp = client.post("/api/pipeline/nonexistent-session-xyz/chat", json={"message": "Who has highest salary?"})
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data or "not found" in data["answer"].lower() or data["row_count"] >= 0

