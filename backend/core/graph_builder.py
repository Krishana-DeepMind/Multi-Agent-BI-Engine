from typing import Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.core.state import AgentSwarmState
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.agents.ingestion_node import ingestion_node
from backend.agents.cleaning_node import cleaning_node
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import analytics_node
from backend.agents.layout_node import layout_node


def build_pipeline_graph(db_engine: DuckDBEngine, llm_router: LLMRouter, checkpointer: Optional[MemorySaver] = None):
    """
    Builds and compiles the LangGraph pipeline for the BI Engine.
    Wires: Ingestion -> Cleaning -> Feature -> Analytics -> Layout -> END.
    Sprint 4: layout_node now runs after analytics_node.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    workflow = StateGraph(AgentSwarmState)

    # Dependency-injected wrappers
    async def run_ingestion(state: AgentSwarmState):
        return await ingestion_node(state, llm_router, db_engine)

    async def run_cleaning(state: AgentSwarmState):
        return await cleaning_node(state, db_engine, llm_router)

    async def run_feature(state: AgentSwarmState):
        return await feature_node(state, db_engine, llm_router)

    async def run_analytics(state: AgentSwarmState):
        return await analytics_node(state, db_engine, llm_router)

    async def run_layout(state: AgentSwarmState):
        return await layout_node(state, llm_router)

    # Register nodes
    workflow.add_node("ingestion", run_ingestion)
    workflow.add_node("cleaning", run_cleaning)
    workflow.add_node("feature", run_feature)
    workflow.add_node("analytics", run_analytics)
    workflow.add_node("layout", run_layout)

    # Set entry point
    workflow.set_entry_point("ingestion")

    # Add edges: full Sprint 4 pipeline
    workflow.add_edge("ingestion", "cleaning")
    workflow.add_edge("cleaning", "feature")
    workflow.add_edge("feature", "analytics")
    workflow.add_edge("analytics", "layout")
    workflow.add_edge("layout", END)

    return workflow.compile(checkpointer=checkpointer)
