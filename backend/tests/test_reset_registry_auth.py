"""POST /admin/reset-registry must not be callable without the admin key."""
import pytest
from fastapi.testclient import TestClient

KEY = "test_admin_secret_12345"


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setenv("ADMIN_SECRET_KEY", KEY)
    from app.main import app
    fake_db.collection("system").document("model_registry").set({"models": {"m": {}}, "champions": {}})
    return TestClient(app)


def test_reset_registry_rejects_anonymous(client, fake_db):
    assert client.post("/admin/reset-registry").status_code == 403
    assert fake_db.raw("system", "model_registry") is not None  # untouched


def test_reset_registry_rejects_wrong_key(client, fake_db):
    assert client.post("/admin/reset-registry", headers={"X-Admin-Key": "nope"}).status_code == 403
    assert fake_db.raw("system", "model_registry") is not None


def test_reset_registry_with_key(client, fake_db):
    r = client.post("/admin/reset-registry", headers={"X-Admin-Key": KEY})
    assert r.status_code == 200
    assert fake_db.raw("system", "model_registry") is None


def test_windows_model_path_normalised(tmp_path, monkeypatch):
    import os
    import app.models.xgboost_model as xm
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_download(gcs_path, local_path):
        seen["local"] = local_path
        raise FileNotFoundError("stop here")

    monkeypatch.setattr(xm, "_download_from_gcs", fake_download)
    with pytest.raises(FileNotFoundError):
        xm.XGBoostDecisionModel.load("runs\\BTCUSDT_v1\\xgboost_models.joblib")
    assert seen["local"] == "runs/BTCUSDT_v1/xgboost_models.joblib"
