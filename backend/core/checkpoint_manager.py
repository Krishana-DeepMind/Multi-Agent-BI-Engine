import os
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import uuid

from backend.core.state import AgentSwarmState

logger = logging.getLogger("checkpoint_manager")

# In-memory & disk fallback storage for checkpoints when DB is offline
_MEMORY_CHECKPOINTS: Dict[str, List[Dict[str, Any]]] = {}
_DISK_CHECKPOINT_DIR = "tmp_storage/checkpoints"


class CheckpointManager:
    """
    Manages saving and loading AgentSwarmState checkpoints per node execution.
    Supports PostgreSQL (asyncpg) with automatic fallback to JSON disk/memory storage.
    """

    def __init__(self):
        os.makedirs(_DISK_CHECKPOINT_DIR, exist_ok=True)

    async def save_checkpoint(
        self,
        session_id: str,
        node_name: str,
        state: Any,
        tokens_used: int = 0
    ) -> Dict[str, Any]:
        """
        Saves a checkpoint snapshot of the state after a node completes.
        """
        if hasattr(state, "model_dump"):
            state_dict = state.model_dump(mode="json")
        elif isinstance(state, dict):
            state_dict = state
        else:
            state_dict = dict(state)

        # Convert UUIDs/datetimes to serializable strings
        session_str = str(session_id)
        state_dict["session_id"] = session_str
        state_dict["pipeline_status"] = state_dict.get("pipeline_status", node_name)
        state_dict["current_agent"] = node_name
        state_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

        checkpoint_record = {
            "checkpoint_id": str(uuid.uuid4()),
            "session_id": session_str,
            "agent_name": node_name,
            "state_json": state_dict,
            "tokens_used": tokens_used,
            "checkpoint_at": datetime.now(timezone.utc).isoformat()
        }

        # 1. Update In-Memory Cache
        if session_str not in _MEMORY_CHECKPOINTS:
            _MEMORY_CHECKPOINTS[session_str] = []
        _MEMORY_CHECKPOINTS[session_str].append(checkpoint_record)

        # 2. Persist to Disk File
        session_dir = os.path.join(_DISK_CHECKPOINT_DIR, session_str)
        os.makedirs(session_dir, exist_ok=True)
        checkpoint_file = os.path.join(session_dir, f"{node_name}.json")
        latest_file = os.path.join(session_dir, "latest.json")

        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_record, f, indent=2, default=str)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_record, f, indent=2, default=str)

        # 3. Try PostgreSQL DB persistence
        try:
            from backend.core.database import save_pipeline_checkpoint
            await save_pipeline_checkpoint(
                session_id=session_str,
                agent_name=node_name,
                state_json=state_dict,
                tokens_used=tokens_used
            )
            logger.info(f"Checkpoint saved to DB for session {session_str} at node '{node_name}'")
        except Exception as e:
            logger.debug(f"DB Checkpoint fallback active ({e}). Saved disk checkpoint for session {session_str} at node '{node_name}'.")

        return checkpoint_record

    async def load_latest_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Loads the latest state checkpoint for a session_id.
        Checks DB first, then falls back to local disk and memory.
        """
        session_str = str(session_id)

        # 1. Try DB first
        try:
            from backend.core.database import get_latest_checkpoint
            db_ps = await get_latest_checkpoint(session_str)
            if db_ps and db_ps.state_json:
                logger.info(f"Loaded DB checkpoint for session {session_str} from agent '{db_ps.agent_name}'")
                return db_ps.state_json
        except Exception as e:
            logger.debug(f"DB load fallback ({e}). Checking local storage for session {session_str}.")

        # 2. Try disk checkpoint
        latest_file = os.path.join(_DISK_CHECKPOINT_DIR, session_str, "latest.json")
        if os.path.exists(latest_file):
            try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    rec = json.load(f)
                    return rec.get("state_json")
            except Exception as e:
                logger.warning(f"Failed to read disk checkpoint {latest_file}: {e}")

        # 3. Try memory cache
        if session_str in _MEMORY_CHECKPOINTS and _MEMORY_CHECKPOINTS[session_str]:
            return _MEMORY_CHECKPOINTS[session_str][-1].get("state_json")

        return None

    async def list_checkpoints(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Lists all saved checkpoints for a session.
        """
        session_str = str(session_id)
        session_dir = os.path.join(_DISK_CHECKPOINT_DIR, session_str)
        results = []

        if os.path.exists(session_dir):
            for fname in sorted(os.listdir(session_dir)):
                if fname.endswith(".json") and fname != "latest.json":
                    try:
                        with open(os.path.join(session_dir, fname), "r", encoding="utf-8") as f:
                            results.append(json.load(f))
                    except Exception:
                        pass
        return results


checkpoint_manager = CheckpointManager()
