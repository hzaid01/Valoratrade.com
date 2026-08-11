"""
Server Runtime Verification Script
Uses FastAPI TestClient to test that app.main:app boots completely and serves API endpoints cleanly.
"""
import os
import sys

# Environment variables setup
os.environ["ADMIN_SECRET_KEY"] = "valora_admin_secret_key_2026"
os.environ["ENCRYPTION_SECRET"] = "valora_encryption_secret_key_2026"
os.environ["BINANCE_API_KEY"] = "test_binance_key"
os.environ["BINANCE_API_SECRET"] = "test_binance_secret"
os.environ["DEBUG"] = "true"

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from app.main import app

def test_app_runtime():
    print("=" * 60)
    print("  INITIALIZING FASTAPI TESTCLIENT (BOOTING APP.MAIN:APP)")
    print("=" * 60)

    client = TestClient(app)

    # 1. Test Root Endpoint
    response_root = client.get("/")
    print(f"GET / -> Status: {response_root.status_code}, Body: {response_root.json()}")
    assert response_root.status_code == 200
    assert response_root.json()["status"] == "running"

    # 2. Test Health Endpoint
    response_health = client.get("/health")
    print(f"GET /health -> Status: {response_health.status_code}, Body: {response_health.json()}")
    assert response_health.status_code == 200
    assert response_health.json()["status"] == "healthy"

    # 3. Test Architecture Endpoint
    response_arch = client.get("/api/architecture")
    print(f"GET /api/architecture -> Status: {response_arch.status_code}, Body: {response_arch.json()}")
    assert response_arch.status_code == 200
    assert "37 causal indicators" in response_arch.json()["data"]["model_stack"]["representation"]

    # 4. Test Admin Killswitch Status Endpoint
    response_ks = client.get("/api/admin/killswitch/status")
    print(f"GET /api/admin/killswitch/status -> Status: {response_ks.status_code}, Body: {response_ks.json()}")
    assert response_ks.status_code == 200
    assert "is_killed" in response_ks.json()["data"]

    print("=" * 60)
    print("  FASTAPI APPLICATION IS FULLY RUNNING AND RESPONDING NATIVE 200 OK")
    print("=" * 60)

if __name__ == "__main__":
    test_app_runtime()
