import io
import json
import pytest
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from backend.core.file_validator import detect_file_type_magic, estimate_row_count, FileValidationError


def test_detect_parquet_magic():
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    buf = io.BytesIO()
    df.write_parquet(buf)
    parquet_bytes = buf.getvalue()

    file_type = detect_file_type_magic(parquet_bytes, "sample.bin")
    assert file_type == "parquet"
    assert estimate_row_count(parquet_bytes, "parquet") == 3


def test_detect_csv_magic():
    csv_bytes = b"id,name,value\n1,Alice,100\n2,Bob,200\n3,Charlie,300\n"
    file_type = detect_file_type_magic(csv_bytes, "data.unknown")
    assert file_type == "csv"
    assert estimate_row_count(csv_bytes, "csv") == 3


def test_detect_json_magic():
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    json_bytes = json.dumps(data).encode("utf-8")
    file_type = detect_file_type_magic(json_bytes, "records")
    assert file_type == "json"
    assert estimate_row_count(json_bytes, "json") == 2


def test_detect_xlsx_magic():
    df = pl.DataFrame({"product": ["A", "B"], "sales": [500, 750]})
    buf = io.BytesIO()
    df.write_excel(buf)
    xlsx_bytes = buf.getvalue()

    file_type = detect_file_type_magic(xlsx_bytes, "sales.data")
    assert file_type == "xlsx"
    assert estimate_row_count(xlsx_bytes, "xlsx") == 2


def test_reject_invalid_magic_bytes():
    # PNG signature disguised as csv
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    with pytest.raises(FileValidationError):
        detect_file_type_magic(png_bytes, "data.csv")

    # Binary zero bytes
    bad_bytes = b"\x00\x01\x02\x03\x00\x00\x00\x00"
    with pytest.raises(FileValidationError):
        detect_file_type_magic(bad_bytes, "test.csv")

    # Empty bytes
    with pytest.raises(FileValidationError):
        detect_file_type_magic(b"", "empty.csv")
