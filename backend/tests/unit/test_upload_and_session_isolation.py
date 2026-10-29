import io
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.api.pipeline import session_store

client = TestClient(app)


class TestUploadAndSessionIsolation:
    def test_upload_creates_new_isolated_session(self):
        """Uploading a dataset creates a unique session with isolated storage and session_store entry."""
        csv1 = b"id,val,category\n1,100,A\n2,200,B\n3,300,C\n"
        files1 = {"file": ("data_a.csv", csv1, "text/csv")}
        res1 = client.post("/api/upload", files=files1)
        assert res1.status_code == 201
        data1 = res1.json()
        session_id_1 = data1["session_id"]
        assert session_id_1 in session_store
        session_1 = session_store[session_id_1]
        assert session_1["file_name"] == "data_a.csv"
        assert session_1["row_count"] == 3
        assert os.path.exists(session_1["file_path"])

    def test_multi_session_isolation_without_overwriting(self):
        """Multiple dataset uploads create isolated sessions and files without overwriting each other."""
        csv1 = b"patient_id,diagnosis,cost\nP1,Cardio,500\nP2,Neuro,900\n"
        files1 = {"file": ("patients.csv", csv1, "text/csv")}
        res1 = client.post("/api/upload", files=files1)
        assert res1.status_code == 201
        sid1 = res1.json()["session_id"]

        csv2 = b"order_id,product,revenue\nO101,Widgets,1200\nO102,Gadgets,4500\nO103,Parts,300\n"
        files2 = {"file": ("retail_orders.csv", csv2, "text/csv")}
        res2 = client.post("/api/upload", files=files2)
        assert res2.status_code == 201
        sid2 = res2.json()["session_id"]

        # 1. Session IDs must be distinct
        assert sid1 != sid2

        # 2. Both sessions exist independently in session_store
        assert sid1 in session_store
        assert sid2 in session_store

        entry1 = session_store[sid1]
        entry2 = session_store[sid2]

        assert entry1["file_name"] == "patients.csv"
        assert entry1["row_count"] == 2
        assert "patients.csv" in entry1["file_path"]

        assert entry2["file_name"] == "retail_orders.csv"
        assert entry2["row_count"] == 3
        assert "retail_orders.csv" in entry2["file_path"]

        # 3. File contents at their respective paths must remain intact and isolated
        with open(entry1["file_path"], "rb") as f:
            assert f.read() == csv1

        with open(entry2["file_path"], "rb") as f:
            assert f.read() == csv2

    def test_failed_upload_preserves_existing_dashboard_sessions(self):
        """A failed upload (empty file or invalid binary) does not corrupt or overwrite active sessions."""
        # Active session
        valid_csv = b"item,qty\nApple,10\nBanana,20\n"
        res_ok = client.post("/api/upload", files={"file": ("good.csv", valid_csv, "text/csv")})
        assert res_ok.status_code == 201
        good_sid = res_ok.json()["session_id"]
        assert good_sid in session_store

        # Attempt invalid upload (empty file)
        res_err1 = client.post("/api/upload", files={"file": ("empty.csv", b"", "text/csv")})
        assert res_err1.status_code == 400

        # Attempt invalid upload (binary executable disguised as csv)
        res_err2 = client.post("/api/upload", files={"file": ("malicious.csv", b"\x7fELF\x02\x01\x01", "text/csv")})
        assert res_err2.status_code == 400

        # Original session must remain healthy and untouched
        assert good_sid in session_store
        assert session_store[good_sid]["file_name"] == "good.csv"
        with open(session_store[good_sid]["file_path"], "rb") as f:
            assert f.read() == valid_csv
