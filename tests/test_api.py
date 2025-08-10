"""
Tests for FastAPI endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.api.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


def test_root(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "AUREA Orchestrator"


def test_health(client):
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "version" in data


@patch("app.api.main.redis_client")
@patch("app.api.main.supabase_client")
def test_create_task(mock_supabase, mock_redis, client):
    """Test task creation."""
    mock_redis.enqueue_task = AsyncMock(return_value=True)
    mock_supabase.create_task = AsyncMock(return_value={"id": "test-id"})
    
    response = client.post(
        "/tasks",
        json={
            "type": "gen_content",
            "payload": {"prompt": "test"},
            "priority": 100
        },
        headers={"Authorization": "Bearer test"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "queued"


def test_metrics(client):
    """Test metrics endpoint."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "aurea_queue_depth" in response.text