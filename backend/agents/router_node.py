import json
from backend.core.state import AgentSwarmState
from backend.core.context_slicer import slice_context
from backend.core.llm_router import LLMRouter, TaskType
from backend.agents.prompts import ROUTER_SYSTEM_PROMPT

# Instantiate the mock router for now
llm_router = LLMRouter()

def format_router_user_msg(ctx: dict) -> str:
    """Format the sliced context for the intent router."""
    return f"""
    Raw Query: {ctx.get('raw_query')}
    File Type: {ctx.get('file_type')}
    Row Count: {ctx.get('raw_row_count')}
    Column Count: {ctx.get('raw_col_count')}
    """

def parse_json_response(content: str) -> dict:
    """Parse JSON response, handling potential markdown fencing."""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return json.loads(content.strip())

async def router_node(state: AgentSwarmState) -> AgentSwarmState:
    """
    LangGraph node function that routes the user's query
    and identifies intent, domain, key entities, and time dimension.
    """
    ctx = slice_context(state, "router")
    
    response = await llm_router.route(
        task_type=TaskType.INTENT_ROUTING,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user",   "content": format_router_user_msg(ctx)}
        ],
        max_tokens=256
    )
    
    parsed = parse_json_response(response["content"])
    
    # Update the state, taking care to preserve all other immutable Pydantic fields
    state_dict = state.model_dump()
    state_dict["intent_class"] = parsed.get("intent", "trend_analysis")
    state_dict["business_domain"] = parsed.get("domain", "unknown")
    state_dict["key_entities"] = parsed.get("key_entities", [])
    state_dict["time_dimension"] = parsed.get("time_dimension")
    state_dict["pipeline_status"] = "routing"
    state_dict["current_agent"] = "router"
    
    # Return a new AgentSwarmState instance
    return AgentSwarmState(**state_dict)
