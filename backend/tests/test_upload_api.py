import io
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_health_endpoints():
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "online"

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"


def test_upload_valid_csv():
    csv_content = b"order_id,amount,region\n101,45.5,US\n102,99.0,EU\n103,120.0,APAC\n"
    files = {"file": ("orders.csv", csv_content, "text/csv")}
    data = {"user_id": "11111111-1111-1111-1111-111111111111"}

    response = client.post("/api/upload", files=files, data=data)
    assert response.status_code == 201

    payload = response.json()
    assert "session_id" in payload
    assert payload["file_type"] == "csv"
    assert payload["file_size_mb"] > 0
    assert payload["row_count_estimate"] == 3
    assert "11111111-1111-1111-1111-111111111111" in payload["file_path"]


def test_upload_valid_json():
    json_content = b'[{"user": "alice", "score": 95}, {"user": "bob", "score": 88}]'
    files = {"file": ("scores.json", json_content, "application/json")}

    response = client.post("/api/upload", files=files)
    assert response.status_code == 201

    payload = response.json()
    assert payload["file_type"] == "json"
    assert payload["row_count_estimate"] == 2


def test_upload_invalid_magic_bytes():
    # An executable or binary masquerading as csv
    fake_csv = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    files = {"file": ("malicious.csv", fake_csv, "text/csv")}

    response = client.post("/api/upload", files=files)
    assert response.status_code == 400
    assert "Unsupported or invalid file format" in response.json()["detail"] or "Binary data" in response.json()["detail"]


def test_upload_empty_file():
    files = {"file": ("empty.csv", b"", "text/csv")}
    response = client.post("/api/upload", files=files)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
