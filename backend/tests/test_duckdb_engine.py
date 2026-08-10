import io
import os
import json
import pytest
import polars as pl
from backend.core.duckdb_engine import DuckDBEngine


@pytest.fixture
def engine():
    eng = DuckDBEngine()
    yield eng
    eng.close()


def test_duckdb_engine_init(engine):
    assert engine.conn is not None
    assert engine.current_table is None


def test_duckdb_load_csv_and_describe(engine):
    csv_bytes = b"customer_id,city,revenue\n1,NYC,150.50\n2,SF,250.00\n3,NYC,300.25\n"
    res = engine.load_from_bytes(csv_bytes, "csv", table_name="customers")

    assert res["success"] is True
    assert res["row_count"] == 3
    assert res["column_count"] == 3
    assert "revenue" in res["columns"]
    assert engine.current_table == "customers"

    profile = engine.get_schema_profile()
    assert "Table: `customers`" in profile
    assert "customer_id" in profile
    assert "revenue" in profile
    assert "Sample Rows" in profile


def test_duckdb_statistical_summary(engine):
    csv_bytes = b"id,score,category\n1,10.5,A\n2,20.0,B\n3,30.5,A\n4,40.0,B\n"
    engine.load_from_bytes(csv_bytes, "csv", table_name="test_summary")

    summary = engine.get_statistical_summary()
    assert "Statistical Summary for `test_summary`" in summary
    assert "score" in summary
    assert "category" in summary


def test_duckdb_execute_validated(engine):
    csv_bytes = b"id,val\n1,10\n2,20\n3,30\n"
    engine.load_from_bytes(csv_bytes, "csv", table_name="data")

    # Valid query
    res = engine.execute_validated("SELECT SUM(val) as total FROM data")
    assert res["success"] is True
    assert res["row_count"] == 1
    assert res["data"][0]["total"] == 60
    assert res["error"] is None
    assert res["ms"] >= 0

    # Invalid query
    bad_res = engine.execute_validated("SELECT non_existent_column FROM data")
    assert bad_res["success"] is False
    assert bad_res["error"] is not None
    assert bad_res["row_count"] == 0


def test_duckdb_write_to_parquet(engine, tmp_path):
    csv_bytes = b"col1,col2\n100,apple\n200,banana\n"
    engine.load_from_bytes(csv_bytes, "csv", table_name="export_test")

    out_file = str(tmp_path / "output.parquet")
    path = engine.write_to_parquet(out_file)
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0

    # Verify written parquet
    df = pl.read_parquet(path)
    assert df.height == 2
    assert "apple" in df["col2"].to_list()
