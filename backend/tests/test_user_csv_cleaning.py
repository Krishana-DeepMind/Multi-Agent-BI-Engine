import io
import polars as pl
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.agents.cleaning_node import generate_heuristic_cleaning_ops, apply_polars_cleaning_op

TEST_CSV = """Customer ID,Full Name,email_Address,Age,SIGNUP_DATE,Country,purchase_amount,Phone Number,gender,Rating (1-5),Last Login,Is_Active
1,John Smith,john@email.com,28,2023-01-15,USA,$120.50,+1-555-867-5309,M,4,2024-01-10,true
2,Jane Doe,jane.doe@email.com,thirty-four,15/02/2023,US,95.00,,F,5,2024-02-15,Y
3,,invalid-email,45,2023/03/20,United States,$200.00,555-0103,m,3,2024-03-01,1
4,Bob Wilson,bob@company.org,,03-20-2023,uk,N/A,+44-20-7946-0958,Male,N/A,,n
5,Alice Brown,alice@test.com,29,May 18 2023,GB,£150.25,,f,4,2024-05-20,active
6,Charlie Day,charlie@test.com,33,2023-06-10,USA,$180.75,+91-98765-43210,M,4,2024-06-01,yes
7,Diana Prince,diana@test.com,27,2023-07-15,United States,$90.00,+1-212-555-1234,F,5,2024-07-20,true
8,Eve Adams,eve@test.com,41,2023-08-20,uk,$310.00,+44-7911-123456,f,3,2024-08-15,Y
9,,bob2@test.com,38,2023-09-10,US,$75.50,555-0199,Male,4,2024-09-10,active
10,Frank Zhou,frank@test.com,,2023-10-05,GB,£220.00,,m,5,2024-10-01,1
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
        current_cols = lf.collect_schema().names()
        if op.column not in current_cols and op.column != "all":
            continue
        lf, code = apply_polars_cleaning_op(lf, op)
        
    df = lf.collect()
    
    # 5. Assert improvements on this dataset (10 rows for proper profiling):
    
    # (a) Gender normalization (10 rows means unique_pct < 0.95, so not protected)
    if "gender" in df.columns:
        genders = df["gender"].to_list()
        expected_genders = ["male", "female", "male", "male", "female",
                           "male", "female", "female", "male", "male"]
        assert genders == expected_genders, f"Gender mismatch: {genders}"
    
    # (b) Boolean standardization
    if "Is_Active" in df.columns:
        active_flags = df["Is_Active"].to_list()
        expected_active = ["yes", "yes", "yes", "no", "yes",
                          "yes", "yes", "yes", "yes", "yes"]
        assert active_flags == expected_active, f"Is_Active mismatch: {active_flags}"
    
    # (c) Currency symbol stripping and safe cast
    if "purchase_amount" in df.columns:
        amounts = df["purchase_amount"].to_list()
        assert amounts[0] == 120.50
        assert amounts[1] == 95.00
        assert amounts[2] == 200.00
        assert amounts[4] == 150.25
        assert amounts[5] == 180.75
    
    # (d) Phone number cleaning — country-aware normalization
    if "Phone Number" in df.columns:
        phones = df["Phone Number"].to_list()
        # +1-555-867-5309 → strip +1, local '5558675309' is 10 digits (valid US)
        assert phones[0] == "5558675309", f"Phone[0] mismatch: {phones[0]}"
        # Missing → NULL (not "Unspecified")
        assert phones[1] is None, f"Phone[1] should be None, got: {phones[1]}"
        # 555-0103 → 7 digits, valid in generic range
        assert phones[2] == "5550103", f"Phone[2] mismatch: {phones[2]}"
        # +44-20-7946-0958 → strip +44, local '2079460958' is 10 digits (valid UK)
        assert phones[3] == "2079460958", f"Phone[3] mismatch: {phones[3]}"
        # Missing → NULL
        assert phones[4] is None, f"Phone[4] should be None, got: {phones[4]}"
        # +91-98765-43210 → strip +91, local '9876543210' is 10 digits (valid India)
        assert phones[5] == "9876543210", f"Phone[5] mismatch: {phones[5]}"
        # +1-212-555-1234 → strip +1, local '2125551234' is 10 digits (valid US)
        assert phones[6] == "2125551234", f"Phone[6] mismatch: {phones[6]}"
    
    # (e) Customer Name placeholder
    if "Full Name" in df.columns:
        names = df["Full Name"].to_list()
        assert names[2] == "Unknown Customer", f"Name[2] mismatch: {names[2]}"
        assert names[8] == "Unknown Customer", f"Name[8] mismatch: {names[8]}"
    
    # (f) Age — missing values preserved as NULL (not imputed)
    if "Age" in df.columns:
        ages = df["Age"].to_list()
        # "thirty-four" (row 2) → NULL after numeric cast (not a number)
        # Row 4 (empty) → NULL
        # Row 10 (empty) → NULL
        # Principled handling: these nulls are NOT imputed
        null_age_count = ages.count(None)
        assert null_age_count >= 2, f"Expected at least 2 null ages, got {null_age_count}. Ages: {ages}"

    print("All user CSV assertions PASSED successfully!")

if __name__ == "__main__":
    test_user_csv_end_to_end()
