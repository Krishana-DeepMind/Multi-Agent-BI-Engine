import os
import io
import uuid
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "datasets")


class SupabaseService:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None, bucket: Optional[str] = None):
        self.url = url or SUPABASE_URL
        self.key = key or SUPABASE_KEY
        self.bucket = bucket or SUPABASE_STORAGE_BUCKET
        self._client = None

        if self.url and self.key:
            try:
                from supabase import create_client
                self._client = create_client(self.url, self.key)
            except Exception as e:
                logger.warning(f"Failed to initialize Supabase client: {e}. Running in local mock mode.")
        else:
            logger.info("Supabase credentials not configured. Running in local fallback mode.")

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def upload_file_bytes(
        self,
        file_path: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
        bucket: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Uploads file bytes to Supabase Storage at path `{user_id}/{session_id}/{filename}`.
        """
        target_bucket = bucket or self.bucket

        if not self._client:
            logger.info(f"[Mock Supabase] Uploaded {len(file_bytes)} bytes to {target_bucket}/{file_path}")
            return {
                "success": True,
                "bucket": target_bucket,
                "path": file_path,
                "size_bytes": len(file_bytes),
                "mock": True
            }

        try:
            # Ensure bucket exists or attempt upload
            response = self._client.storage.from_(target_bucket).upload(
                path=file_path,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": "true"}
            )
            return {
                "success": True,
                "bucket": target_bucket,
                "path": file_path,
                "response": response
            }
        except Exception as e:
            logger.error(f"Error uploading to Supabase Storage: {e}")
            raise RuntimeError(f"Supabase Storage upload failed: {str(e)}")

    async def create_session_record(
        self,
        session_id: str,
        user_id: Optional[str],
        raw_file_path: str,
        file_type: str,
        status: str = "initiated"
    ) -> Dict[str, Any]:
        """
        Inserts a row into the sessions table.
        """
        data = {
            "id": session_id,
            "status": status,
            "raw_file_path": raw_file_path,
            "file_type": file_type,
        }
        if user_id:
            data["user_id"] = user_id

        if not self._client:
            logger.info(f"[Mock Supabase] Created session row: {data}")
            return {"success": True, "data": data, "mock": True}

        try:
            res = self._client.table("sessions").insert(data).execute()
            return {"success": True, "data": res.data}
        except Exception as e:
            logger.error(f"Error creating session record: {e}")
            # If table/FK constraint issue (e.g. auth.users FK when user_id is dummy), log and retry or raise
            raise RuntimeError(f"Database session creation failed: {str(e)}")

    def get_signed_url(self, file_path: str, expires_in: int = 3600, bucket: Optional[str] = None) -> str:
        """
        Generate a signed URL for reading a file from Supabase Storage.
        """
        target_bucket = bucket or self.bucket
        if not self._client:
            return f"https://mock-supabase.local/storage/v1/object/sign/{target_bucket}/{file_path}?token=mock"

        try:
            res = self._client.storage.from_(target_bucket).create_signed_url(file_path, expires_in)
            return res.get("signedURL") or res.get("signedUrl") or str(res)
        except Exception as e:
            logger.error(f"Error generating signed URL: {e}")
            raise RuntimeError(f"Failed to generate signed URL: {str(e)}")


supabase_service = SupabaseService()
