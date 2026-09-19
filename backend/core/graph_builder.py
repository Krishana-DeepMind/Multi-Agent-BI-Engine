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

def build_pipeline_graph(db_engine: DuckDBEngine, llm_router: LLMRouter, checkpointer: Optional[MemorySaver] = None):
    """
    Builds and compiles the LangGraph pipeline for the BI Engine.
    For this sprint, it wires Ingestion -> Cleaning.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()
        
    # Initialize Graph with our state schema
    workflow = StateGraph(AgentSwarmState)
    
    # Define wrappers to inject our dependencies (db_engine, llm_router)
    async def run_ingestion(state: AgentSwarmState):
        return await ingestion_node(state, db_engine, llm_router)
        
    async def run_cleaning(state: AgentSwarmState):
        return await cleaning_node(state, db_engine, llm_router)
        
    async def run_feature(state: AgentSwarmState):
        return await feature_node(state, db_engine, llm_router)
        
    async def run_analytics(state: AgentSwarmState):
        return await analytics_node(state, db_engine, llm_router)
        
    # Add nodes
    workflow.add_node("ingestion", run_ingestion)
    workflow.add_node("cleaning", run_cleaning)
    workflow.add_node("feature", run_feature)
    workflow.add_node("analytics", run_analytics)
    
    # Set entry point
    workflow.set_entry_point("ingestion")
    
    # Add edges
    workflow.add_edge("ingestion", "cleaning")
    workflow.add_edge("cleaning", "feature")
    workflow.add_edge("feature", "analytics")
    workflow.add_edge("analytics", END)
    
    # Compile graph
    app = workflow.compile(checkpointer=checkpointer)
    
    return app
