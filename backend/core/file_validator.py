import io
import json
import zipfile
import pyarrow.parquet as pq
import polars as pl
from typing import Tuple, Optional


class FileValidationError(Exception):
    """Raised when file validation fails."""
    pass


def detect_file_type_magic(file_bytes: bytes, filename: Optional[str] = None) -> str:
    """
    Detect file format by inspecting magic bytes / signatures instead of trusting extension.
    Supported types: 'parquet', 'xlsx', 'json', 'csv'.
    Raises FileValidationError if the format is unsupported or invalid.
    """
    if not file_bytes or len(file_bytes.strip()) == 0:
        raise FileValidationError("Uploaded file is empty.")

    # 1. Parquet Magic Bytes: Starts with b"PAR1"
    if file_bytes.startswith(b"PAR1"):
        return "parquet"

    # 2. Excel (.xlsx): ZIP archive starting with PK\x03\x04 and containing xl/ or [Content_Types].xml
    if file_bytes.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                namelist = z.namelist()
                if any(name.startswith("xl/") or name == "[Content_Types].xml" for name in namelist):
                    return "xlsx"
        except Exception:
            pass

    # 3. JSON: Check if valid UTF-8/ASCII JSON structure starting with [ or {
    # Check leading non-whitespace characters
    stripped = file_bytes.strip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        try:
            text = file_bytes.decode("utf-8")
            parsed = json.loads(text)
            if isinstance(parsed, (list, dict)):
                return "json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    # Also check for JSON Lines (NDJSON)
    if stripped.startswith(b"{"):
        try:
            first_line = stripped.split(b"\n")[0].decode("utf-8")
            parsed_line = json.loads(first_line)
            if isinstance(parsed_line, dict):
                return "json"
        except Exception:
            pass

    # 4. CSV: Plain text with delimiters and consistent tabular layout
    try:
        # Check text decodability
        text = None
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                text = file_bytes[:10000].decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if text is not None:
            # Quick check for binary/null bytes (which indicate non-text formats like executables or images)
            if "\x00" in text:
                raise FileValidationError("Binary data detected in text file.")

            # Test parsing with Polars to confirm tabular structure
            try:
                sample_io = io.BytesIO(file_bytes[:65536])
                df_sample = pl.read_csv(sample_io, n_rows=5, ignore_errors=True, truncate_ragged_lines=True)
                if df_sample.width >= 1:
                    return "csv"
            except Exception:
                # If single column or simple comma/delimiter text
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) >= 1:
                    return "csv"
    except Exception:
        pass

    raise FileValidationError(
        "Unsupported or invalid file format. Supported types: csv, xlsx, json, parquet."
    )


def estimate_row_count(file_bytes: bytes, file_type: str) -> int:
    """
    Fast row count estimator without loading the entire dataset into heavy objects.
    """
    if not file_bytes:
        return 0

    try:
        if file_type == "parquet":
            # Read Parquet footer metadata (instant O(1))
            parquet_file = pq.ParquetFile(io.BytesIO(file_bytes))
            return int(parquet_file.metadata.num_rows)

        elif file_type == "csv":
            # Fast newline counter with header subtraction
            newline_count = file_bytes.count(b"\n")
            if not file_bytes.endswith(b"\n") and len(file_bytes) > 0:
                newline_count += 1
            # Subtract header row if > 1
            return max(0, newline_count - 1) if newline_count > 0 else 0

        elif file_type == "json":
            # Fast parse for JSON array or NDJSON
            stripped = file_bytes.strip()
            if stripped.startswith(b"["):
                parsed = json.loads(file_bytes.decode("utf-8"))
                if isinstance(parsed, list):
                    return len(parsed)
            elif stripped.startswith(b"{"):
                # Could be NDJSON (lines) or single object
                lines = [l for l in stripped.split(b"\n") if l.strip()]
                if len(lines) > 1:
                    return len(lines)
                parsed = json.loads(file_bytes.decode("utf-8"))
                if isinstance(parsed, dict):
                    # If dict has a main records list key
                    for val in parsed.values():
                        if isinstance(val, list):
                            return len(val)
                    return 1
            return 1

        elif file_type == "xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
                sheet = wb.active
                # Count non-empty rows
                row_count = 0
                for row in sheet.iter_rows(values_only=True):
                    if any(cell is not None for cell in row):
                        row_count += 1
                wb.close()
                return max(0, row_count - 1) if row_count > 1 else max(1, row_count)
            except Exception:
                try:
                    df = pl.read_excel(io.BytesIO(file_bytes))
                    return df.height
                except Exception:
                    return 1

    except Exception:
        return 1

    return 1
