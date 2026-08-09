from typing import Dict, Any
from .state import AgentSwarmState

AGENT_CONTEXT_FIELDS = {
    "router":    ["raw_query", "file_type", "raw_row_count", "raw_col_count"],
    "ingestion": ["raw_query", "raw_file_path", "file_type", "intent_class", "business_domain"],
    "cleaning":  ["column_metadata", "raw_row_count", "intent_class", "key_entities"],
    "feature":   ["column_metadata", "cleaning_operations", "intent_class",
                  "business_domain", "key_entities", "time_dimension"],
    "analytics": ["column_metadata", "feature_definitions", "raw_query",
                  "intent_class", "business_domain", "key_entities", "time_dimension"],
    "layout":    ["generated_queries", "query_results", "raw_query",
                  "business_domain", "intent_class"],
    "qa":        ["*"],  # QA agent is the only one that sees full state
}

def slice_context(state: AgentSwarmState, agent_name: str) -> Dict[str, Any]:
    fields = AGENT_CONTEXT_FIELDS.get(agent_name, [])
    state_dict = state.model_dump()
    if fields == ["*"]:
        return state_dict
    return {k: state_dict[k] for k in fields if k in state_dict}
