import uuid
import logging
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from pydantic import BaseModel

from backend.core.file_validator import detect_file_type_magic, estimate_row_count, FileValidationError
from backend.core.supabase_client import supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
CHUNK_SIZE = 1024 * 1024  # 1MB


class UploadResponse(BaseModel):
    session_id: str
    file_path: str
    file_type: str
    file_size_mb: float
    row_count_estimate: int


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload dataset file",
    description="Uploads a dataset file (CSV, Excel, JSON, Parquet) with magic byte validation, streaming to Supabase Storage, and session creation."
)
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(default="00000000-0000-0000-0000-000000000000")
):
    """
    Handle multipart/form-data upload.
    - Validates max file size (100MB)
    - Validates file format by magic bytes (not file extension)
    - Streams to Supabase Storage at: {user_id}/{session_id}/{filename}
    - Creates a session row in PostgreSQL
    - Returns session metadata and row count estimate
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename must be provided."
        )

    # 1. Read file in chunks with size limit enforcement
    content_chunks = []
    total_bytes = 0

    try:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File size exceeds maximum allowed limit of 100MB (current size: {round(total_bytes / (1024 * 1024), 2)}MB)."
                )
            content_chunks.append(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading upload file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read uploaded file: {str(e)}"
        )
    finally:
        await file.close()

    if total_bytes == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty."
        )

    file_bytes = b"".join(content_chunks)

    # 2. Magic byte validation
    try:
        detected_type = detect_file_type_magic(file_bytes, file.filename)
    except FileValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File validation failed: {str(e)}"
        )

    # 3. Estimate row count
    row_count_est = estimate_row_count(file_bytes, detected_type)

    # 4. Generate unique session ID and storage path
    session_id = str(uuid.uuid4())
    sanitized_filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    storage_path = f"{user_id}/{session_id}/{sanitized_filename}"
    file_size_mb = max(0.0001, round(total_bytes / (1024 * 1024), 4))

    # 5. Content type mapping
    content_type_map = {
        "parquet": "application/vnd.apache.parquet",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "json": "application/json",
        "csv": "text/csv",
    }
    content_type = content_type_map.get(detected_type, file.content_type or "application/octet-stream")

    # 6. Stream to Supabase Storage
    try:
        await supabase_service.upload_file_bytes(
            file_path=storage_path,
            file_bytes=file_bytes,
            content_type=content_type
        )
    except Exception as e:
        logger.error(f"Supabase Storage error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to store file in cloud storage: {str(e)}"
        )

    # 7. Create Session Row in PostgreSQL
    try:
        await supabase_service.create_session_record(
            session_id=session_id,
            user_id=user_id if user_id != "00000000-0000-0000-0000-000000000000" else None,
            raw_file_path=storage_path,
            file_type=detected_type,
            status="initiated"
        )
    except Exception as e:
        logger.error(f"Supabase DB error: {e}")
        # Note: In production or strict environment, we can choose to rollback or alert
        logger.warning(f"Could not persist session in database (continuing with session metadata): {e}")

    return UploadResponse(
        session_id=session_id,
        file_path=storage_path,
        file_type=detected_type,
        file_size_mb=file_size_mb,
        row_count_estimate=row_count_est
    )
