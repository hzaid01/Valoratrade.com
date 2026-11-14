"""Admin auth on promote / retrain / force-training (always) and scheduler endpoints (ADMIN_AUTH)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

KEY = "test_admin_secret_12345"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADMIN_SECRET_KEY", KEY)
    import app.api.admin as admin
    import app.api.training as training

    async def noop(*a, **k):
        return {}

    monkeypatch.setattr(training, "run_training_job", noop)

    async def ok_preconditions(symbols):
        return True, "ok"

    monkeypatch.setattr(training, "check_training_preconditions", ok_preconditions)
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(training.router)
    return TestClient(app)


H = {"X-Admin-Key": KEY}


def test_promote_requires_key(client):
    r = client.post("/api/admin/promote", json={"model_id": "nope"})
    assert r.status_code == 403
    r = client.post("/api/admin/promote", json={"model_id": "nope"}, headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 403
    r = client.post("/api/admin/promote", json={"model_id": "nope"}, headers=H)
    assert r.status_code == 400  # authenticated; unknown model


def test_promote_refuses_when_server_has_no_key(client, monkeypatch):
    monkeypatch.delenv("ADMIN_SECRET_KEY")
    r = client.post("/api/admin/promote", json={"model_id": "x"}, headers=H)
    assert r.status_code == 503


def test_retrain_requires_key(client):
    assert client.post("/api/admin/retrain", json={"symbol": "BTC"}).status_code == 403


def test_force_training_always_requires_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH", "log_only")
    body = {"symbols": ["BTCUSDT"], "force": True}
    assert client.post("/api/training/trigger", json=body).status_code == 403
    assert client.post("/api/training/run-sync", json=body).status_code == 403
    r = client.post("/api/training/trigger", json=body, headers=H)
    assert r.status_code == 200 and r.json()["success"] is True


def test_scheduled_trigger_log_only_allows_and_logs(client, monkeypatch, caplog):
    monkeypatch.delenv("ADMIN_AUTH", raising=False)
    with caplog.at_level("WARNING"):
        r = client.post("/api/training/trigger", json={"symbols": ["BTCUSDT"], "force": False})
    assert r.status_code == 200
    assert "ADMIN_AUTH log_only: WOULD HAVE REJECTED" in caplog.text


@pytest.mark.parametrize("path,body", [
    ("/api/training/trigger", {"symbols": ["BTCUSDT"], "force": False}),
    ("/api/training/run-sync", {"symbols": ["BTCUSDT"], "force": False}),
    ("/api/training/forward-cycle", None),
    ("/api/training/ingest-data", None),
])
def test_scheduler_endpoints_enforce(client, monkeypatch, path, body):
    monkeypatch.setenv("ADMIN_AUTH", "enforce")
    r = client.post(path, json=body) if body is not None else client.post(path)
    assert r.status_code == 403


def test_scheduler_trigger_enforce_with_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH", "enforce")
    r = client.post("/api/training/trigger", json={"symbols": ["BTCUSDT"]}, headers=H)
    assert r.status_code == 200


def test_unknown_admin_auth_value_is_log_only(client, monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH", "enforcee")
    r = client.post("/api/training/trigger", json={"symbols": ["BTCUSDT"]})
    assert r.status_code == 200


def test_read_endpoints_stay_open(client):
    assert client.get("/api/training/status").status_code == 200


def test_admin_retrain_actually_runs_training(client, monkeypatch):
    import app.api.training as training
    calls = []

    async def fake_job(symbols, force=False):
        calls.append((symbols, force))
        return {}

    monkeypatch.setattr(training, "run_training_job", fake_job)
    r = client.post("/api/admin/retrain", json={"symbol": "eth"}, headers=H)
    assert r.status_code == 200 and r.json()["data"]["status"] == "queued"
    assert calls == [(["ETHUSDT"], False)]  # old endpoint returned "queued" and did nothing


def test_admin_retrain_skips_when_running(client, monkeypatch):
    import app.api.training as training
    monkeypatch.setitem(training._training_status, "is_running", True)
    r = client.post("/api/admin/retrain", json={"symbol": "BTC"}, headers=H)
    assert r.json()["success"] is False and r.json()["data"]["status"] == "skipped"
