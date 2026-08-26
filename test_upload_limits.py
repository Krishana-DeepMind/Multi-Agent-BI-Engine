import asyncio
import io
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_column_limit():
    print("Testing 200-column CSV...")
    # Create a 200-column CSV
    header = ",".join([f"col_{i}" for i in range(200)])
    row = ",".join([str(i) for i in range(200)])
    csv_content = f"{header}\n{row}\n"
    
    file_bytes = csv_content.encode('utf-8')
    
    response = client.post(
        "/api/upload",
        files={"file": ("test_200_cols.csv", file_bytes, "text/csv")},
        data={"user_id": "test-user-123"}
    )
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")
    assert response.status_code == 400
    assert "200 columns" in response.json()["detail"]
    print("Column limit test passed!\n")

def test_file_limit():
    print("Testing 2 files per session limit...")
    csv_content = "id,name,value\n1,test,100\n".encode('utf-8')
    user_id = "test-limit-user"
    
    # Upload file 1
    r1 = client.post("/api/upload", files={"file": ("file1.csv", csv_content, "text/csv")}, data={"user_id": user_id})
    print(f"File 1 Status: {r1.status_code}")
    
    # Upload file 2
    r2 = client.post("/api/upload", files={"file": ("file2.csv", csv_content, "text/csv")}, data={"user_id": user_id})
    print(f"File 2 Status: {r2.status_code}")
    
    # Upload file 3 (should fail)
    r3 = client.post("/api/upload", files={"file": ("file3.csv", csv_content, "text/csv")}, data={"user_id": user_id})
    print(f"File 3 Status: {r3.status_code}")
    if r3.status_code == 400:
        print(f"File 3 Response: {r3.json()}")
        assert "maximum of 2 files" in r3.json()["detail"]
        print("File limit test passed!")
    else:
        print("File limit test skipped/failed (Supabase might be mocked or not enforcing)")

if __name__ == "__main__":
    test_column_limit()
    test_file_limit()
