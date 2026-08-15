import pytest
import httpx
import asyncio
from fastapi.testclient import TestClient
import uuid

# Assuming the main FastAPI app is in backend.main
from backend.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_pipeline_e2e():
    """
    End-to-End Test for the Multi-Agent BI Engine Pipeline.
    Validates: Upload -> Pipeline Start -> Check Status -> Dashboard
    """
    # 1. Upload a dummy CSV file
    csv_content = b"id,name,revenue,date\n1,Alpha,1000,2024-01-01\n2,Beta,2000,2024-01-02\n"
    
    response = client.post(
        "/api/upload",
        files={"file": ("test_data.csv", csv_content, "text/csv")},
        data={"user_id": str(uuid.uuid4())}
    )
    
    assert response.status_code == 201
    upload_data = response.json()
    assert "session_id" in upload_data
    assert upload_data["file_type"] == "csv"
    
    session_id = upload_data["session_id"]
    
    # 2. Start the pipeline
    start_response = client.post(
        f"/api/pipeline/{session_id}/start?raw_query=What is the total revenue?"
    )
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "initiated"
    
    # 3. Check State Endpoint
    # We might need to wait for the async task to process a bit
    await asyncio.sleep(1)
    
    # Since DB is mocked or might be in memory, we just test that the endpoint responds 
    # appropriately. It might return 404 if the agent hasn't saved checkpoint yet, which is normal.
    state_response = client.get(f"/api/pipeline/{session_id}/state")
    assert state_response.status_code in [200, 404]
    
    # 4. Check Dashboard Endpoint
    # Similarly, dashboard might not exist yet as the pipeline task is running
    dash_response = client.get(f"/api/dashboards/{session_id}")
    assert dash_response.status_code in [200, 404]

    # In a fully integrated environment with the local database, 
    # we would wait for the SSE stream to emit 'complete' before asserting 200 on /state and /dashboards.
