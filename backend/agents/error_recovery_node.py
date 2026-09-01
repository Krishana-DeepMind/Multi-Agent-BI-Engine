import logging
from typing import Dict, Any, List
from datetime import datetime, timezone

from backend.core.state import AgentSwarmState
from backend.core.checkpoint_manager import checkpoint_manager

logger = logging.getLogger("error_recovery_node")


async def error_recovery_node(state: AgentSwarmState, error_info: Dict[str, Any]) -> AgentSwarmState:
    """
    Error Recovery Node.
    Executed when an agent fails max retries.
    - Evaluates error recoverability (e.g. rate limit, connection timeout vs corrupt file)
    - Records detailed error entry into state.errors
    - Updates pipeline_status to 'failed'
    - Saves checkpoint snapshot to checkpoint_manager
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    agent_name = error_info.get("agent", state_dict.get("current_agent", "unknown"))
    raw_error_msg = str(error_info.get("error", "Unspecified error occurred"))

    logger.warning(f"Executing error_recovery_node for agent '{agent_name}'. Error: {raw_error_msg}")

    # Determine recoverability heuristic
    err_lower = raw_error_msg.lower()
    unrecoverable_terms = ["invalid file", "corrupt", "permission denied", "unsupported format", "empty dataset"]
    is_recoverable = not any(term in err_lower for term in unrecoverable_terms)

    error_record = {
        "agent": agent_name,
        "error": raw_error_msg,
        "recoverable": "true" if is_recoverable else "false",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempted": error_info.get("attempted", f"Execution of {agent_name} agent node")
    }

    # Append to errors list
    errors_list = state_dict.get("errors", [])
    errors_list.append(error_record)
    state_dict["errors"] = errors_list
    state_dict["pipeline_status"] = "failed"
    state_dict["current_agent"] = agent_name
    state_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Save checkpoint
    try:
        await checkpoint_manager.save_checkpoint(
            session_id=str(state_dict.get("session_id")),
            node_name=f"{agent_name}_failed",
            state=state_dict
        )
    except Exception as e:
        logger.error(f"Failed to save error checkpoint: {e}")

    return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict
