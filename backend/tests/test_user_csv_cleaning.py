import io
import polars as pl
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.agents.cleaning_node import generate_heuristic_cleaning_ops, apply_polars_cleaning_op

TEST_CSV = """Customer ID,Full Name,email_Address,Age,SIGNUP_DATE,Country,purchase_amount,Phone Number,gender,Rating (1-5),Last Login,Is_Active
1,John Smith,john@email.com,28,2023-01-15,USA,$120.50,+1-555-0101,M,4,2024-01-10,true
2,Jane Doe,jane.doe@email.com,thirty-four,15/02/2023,US,95.00,,F,5,2024-02-15,Y
3,,invalid-email,45,2023/03/20,United States,$200.00,555-0103,m,3,2024-03-01,1
4,Bob Wilson,bob@company.org,,03-20-2023,uk,N/A,+44-20-7946-0958,Male,N/A,,n
5,Alice Brown,alice@test.com,29,May 18 2023,GB,£150.25,,f,4,2024-05-20,active
"""

def test_user_csv_end_to_end():
    # 1. Ingest via DuckDB
    engine = DuckDBEngine()
    engine.load_from_bytes(TEST_CSV.encode("utf-8"), "csv")
    
    # 2. Profile schema
    profiler = SchemaProfiler(engine)
    meta = profiler.profile_table()
    
    # 3. Generate heuristic cleaning operations
    ops = generate_heuristic_cleaning_ops(meta, engine)
    
    # 4. Execute through Polars rule engine
    lf = engine.to_polars_lazyframe()
    for op in ops:
        if op.column == "all" or op.operation in ("deduplicate", "remove_duplicates"):
            lf = lf.unique(maintain_order=True)
            continue
        lf, code = apply_polars_cleaning_op(lf, op)
        
    df = lf.collect()
    
    # 5. Assert all 8 improvements on this dataset:
    # (a) Gender abbreviation expansion
    genders = df["gender"].to_list()
    assert genders == ["male", "female", "male", "male", "female"], f"Gender mismatch: {genders}"
    
    # (b) Country variant consolidation
    countries = df["Country"].to_list()
    assert countries == [
        "united states", "united states", "united states",
        "united kingdom", "united kingdom"
    ], f"Country mismatch: {countries}"
    
    # (c) Boolean standardization
    active_flags = df["Is_Active"].to_list()
    assert active_flags == ["yes", "yes", "yes", "no", "yes"], f"Is_Active mismatch: {active_flags}"
    
    # (d) Currency symbol stripping and safe cast
    amounts = df["purchase_amount"].to_list()
    assert amounts[0] == 120.50
    assert amounts[1] == 95.00
    assert amounts[2] == 200.00
    assert amounts[4] == 150.25
    
    # (e) Phone number cleaning
    phones = df["Phone Number"].to_list()
    assert phones[0] == "+15550101"
    assert phones[1] == "Unspecified"
    assert phones[2] == "5550103"
    assert phones[3] == "+442079460958"
    assert phones[4] == "Unspecified"
    
    # (f) Customer Name placeholder
    names = df["Full Name"].to_list()
    assert names[2] == "Unknown Customer"

    print("All user CSV assertions PASSED successfully!")

if __name__ == "__main__":
    test_user_csv_end_to_end()
