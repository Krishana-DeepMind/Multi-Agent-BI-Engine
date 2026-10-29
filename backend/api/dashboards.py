import logging
import uuid
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.core.database import get_session, Dashboard
from backend.core.supabase_client import supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


@router.get("/{session_id}", summary="Get final dashboard configuration")
async def get_dashboard_config(session_id: str):
    """
    Retrieves the final dashboard configuration from the dashboards table.
    """
    try:
        from backend.core.database import get_db_session
        from sqlalchemy import select

        async with get_db_session() as session:
            result = await session.execute(
                select(Dashboard).filter_by(session_id=uuid.UUID(session_id))
            )
            dashboard = result.scalars().first()

        if not dashboard:
            # Fallback to in-memory session_store or local tmp_storage
            import os, json
            from backend.api.pipeline import session_store
            sess = session_store.get(session_id)
            if not sess:
                disk_path = os.path.join("tmp_storage", session_id, "dashboard.json")
                if os.path.exists(disk_path):
                    try:
                        with open(disk_path, "r", encoding="utf-8") as f:
                            sess = json.load(f)
                    except Exception:
                        pass
            if sess and (sess.get("dashboard_config") or sess.get("charts")):
                cfg = sess.get("dashboard_config") or sess.get("charts", [])
                return {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "title": sess.get("dashboard_title") or sess.get("title", "Dashboard"),
                    "config": cfg,
                    "published": False,
                    "created_at": None,
                }
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dashboard not found for this session."
            )

        return {
            "id": str(dashboard.id),
            "session_id": session_id,
            "title": dashboard.title,
            "config": dashboard.config_json,
            "published": dashboard.published,
            "created_at": dashboard.created_at,
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session_id format."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dashboard for {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dashboard: {str(e)}"
        )


@router.get("/{session_id}/query/{query_id}/data", summary="Get query result data")
async def get_query_data(session_id: str, query_id: str):
    """
    Retrieves the executed query result data.
    Returns a signed URL from Supabase Storage for the frontend to download.
    """
    try:
        # In the blueprint, results are written as: result_{query_id}.parquet
        file_path = f"{session_id}/result_{query_id}.parquet"
        
        # Get signed URL for 'query-results' bucket
        signed_url = supabase_service.get_signed_url(
            file_path=file_path,
            expires_in=3600,
            bucket="query-results"
        )
        
        return {
            "session_id": session_id,
            "query_id": query_id,
            "data_url": signed_url
        }
    except Exception as e:
        logger.error(f"Error generating data URL for query {query_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch query data: {str(e)}"
        )
