from typing import List, Dict, Optional, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, model_validator, UUID4

class ColumnMeta(BaseModel):
    name: str
    original_name: str
    dtype: str
    semantic_type: Literal[
        "identifier", "metric", "dimension", "date", "currency",
        "percentage", "boolean", "text_description", "geographic", "unknown"
    ]
    business_label: str
    null_pct: float = Field(..., ge=0.0, le=1.0)
    unique_pct: float = Field(..., ge=0.0, le=1.0)
    sample_values: List[Any] = Field(..., max_length=5)
    is_primary_key: bool
    is_target_metric: bool

class CleaningOperation(BaseModel):
    column: str
    operation: Literal["fill_null", "remove_outlier", "normalize", "cast_type",
                       "drop_column", "deduplicate", "trim_whitespace", "parse_date"]
    strategy: str
    rows_affected: int
    before_nulls: int
    after_nulls: int
    polars_code: str
    rationale: str

    @model_validator(mode='after')
    def check_nulls(self):
        # The prompt mentioned rows_after <= rows_before, which likely maps to after_nulls <= before_nulls here
        # or maybe the prompt was slightly wrong. We will ensure after_nulls <= before_nulls if applicable
        # (Though sometimes filling nulls makes after_nulls 0 < before_nulls, which is valid).
        if self.after_nulls > self.before_nulls:
            raise ValueError("after_nulls cannot be greater than before_nulls")
        return self

class FeatureDefinition(BaseModel):
    name: str
    polars_expr: str
    sql_expr: str
    rationale: str
    expected_insight: str

class QueryDefinition(BaseModel):
    id: str
    title: str
    business_question: str
    sql: str
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    group_by: Optional[str] = None
    chart_type: Literal["bar", "line", "scatter", "pie", "heatmap",
                        "kpi_card", "data_table", "area", "funnel"]
    insight_summary: str
    priority: int

class QueryResult(BaseModel):
    query_id: str
    result_storage_path: str
    row_count: int
    column_names: List[str]
    sample_rows: List[Dict] = Field(..., max_length=5)
    execution_ms: int
    error: Optional[str] = None

class ChartConfig(BaseModel):
    query_id: str
    echarts_option: Dict
    layout: Dict
    title: str
    subtitle: str
    responsive: bool

class QAReport(BaseModel):
    data_quality_score: float = Field(..., ge=0.0, le=1.0)
    completeness_score: float
    query_validity: Dict[str, bool]
    chart_relevance: Dict[str, float]
    anomalies: List[str]
    suggestions: List[str]
    overall_confidence: float
    approval_status: Literal["approved", "needs_review", "rejected"]
    reviewer_notes: Optional[str] = None

class AgentSwarmState(BaseModel):
    # Pipeline Meta
    session_id: UUID4
    user_id: str
    pipeline_status: Literal[
        "initiated", "routing", "ingesting", "cleaning",
        "featuring", "querying", "layouting", "verifying", "complete", "failed"
    ]
    created_at: str
    updated_at: str
    current_agent: str

    # User Intent
    raw_query: str
    intent_class: Literal["trend_analysis", "root_cause", "comparison",
                          "distribution", "correlation", "ranking", "forecasting"]
    business_domain: Literal["finance", "sales", "operations", "marketing",
                             "hr", "ecommerce", "iot", "unknown"]
    key_entities: List[str]
    time_dimension: Optional[str] = None

    # Ingestion Agent Namespace
    raw_file_path: str
    file_type: Literal["csv", "xlsx", "json", "parquet", "unknown"]
    raw_row_count: int
    raw_col_count: int
    schema_fingerprint: str
    schema_embedding_id: Optional[str] = None
    column_metadata: List[ColumnMeta]
    similar_schemas_found: bool

    # Cleaning Agent Namespace
    cleaning_operations: List[CleaningOperation]
    cleaned_parquet_path: str
    data_quality_score: float
    rows_before: int
    rows_after: int
    columns_dropped: List[str]

    # Feature Architect Namespace
    feature_definitions: List[FeatureDefinition]
    enriched_parquet_path: str
    feature_rationale: str

    # Analytics Engine Namespace
    generated_queries: List[QueryDefinition]
    query_results: List[QueryResult]
    queries_failed: List[str]

    # Layout Agent Namespace
    dashboard_config: List[ChartConfig]
    dashboard_title: str
    dashboard_theme: Literal["light", "dark", "brand"]
    layout_rationale: str

    # QA Agent Namespace
    qa_report: QAReport

    # Error & Retry Handling
    errors: List[Dict[str, str]]
    retry_count: int
    token_usage: Dict[str, int]

    @model_validator(mode='after')
    def check_rows(self):
        if self.rows_after > self.rows_before:
            raise ValueError("rows_after cannot be greater than rows_before")
        return self
