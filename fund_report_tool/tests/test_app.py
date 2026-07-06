import os
import pytest
from fastapi.testclient import TestClient
from app import app

TEST_DB = "data/test_app.db"


@pytest.fixture
def client():
    os.makedirs("data/uploads", exist_ok=True)
    os.makedirs("data/outputs", exist_ok=True)
    return TestClient(app)


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "基金投后数据" in response.text or "bootstrap" in response.text.lower()


def test_api_clean_missing_file(client):
    response = client.post("/api/clean", data={"data_type": "project"})
    assert response.status_code == 422


def test_api_mappings_get(client):
    response = client.get("/api/mappings")
    assert response.status_code == 200
    data = response.json()
    assert "mappings" in data
    assert len(data["mappings"]) > 0


def test_api_clean_batch(client):
    import io
    files = [
        ("files", ("test1.xlsx", io.BytesIO(b"fake"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    ]
    response = client.post("/api/clean-batch", data={"data_type": "project"}, files=files)
    assert response.status_code == 200
    result = response.json()
    assert "results" in result
    assert "total" in result


def test_api_chat_fill_intent(client):
    response = client.post("/api/chat", json={
        "message": "公司名在第一列，指标在第一行，填2024年的数据",
        "context": {"template_path": "data/test.xlsx"}
    })
    assert response.status_code == 200
    data = response.json()
    assert "intent" in data


def test_api_chat_query_intent(client):
    response = client.post("/api/chat", json={
        "message": "公司A近三年的存货情况"
    })
    assert response.status_code == 200
    data = response.json()
    assert "intent" in data


def test_api_projects(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert "projects" in data


def test_api_periods(client):
    response = client.get("/api/periods?project=项目A")
    assert response.status_code == 200
    data = response.json()
    assert "periods" in data


def test_api_data(client):
    response = client.get("/api/data?project=项目A&period=2024-12-31")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


def test_api_metrics_data(client):
    response = client.get("/api/metrics_data?project=项目A&period=2024-12-31")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
