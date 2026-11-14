"""Signals process sees promotions from other instances; cache matches the champion."""
from types import SimpleNamespace

import pytest

import app.models.model_cache as cache_mod
from app.models.registry.model_registry import ModelRegistry
from app.core.system_state import SystemState as S, get_state_manager
from app.models.xgboost_model import ModelPrediction

SYM = "BTCUSDT"


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_mod.clear_cache()
    yield
    cache_mod.clear_cache()


def _reg_model(reg, v):
    reg.register(version=v, symbol=SYM, patch_tst_path="", xgboost_path=f"runs/{v}/m.joblib",
                 dataset_version="d", feature_version="f", training_snapshot="s",
                 training_metrics={}, validation_metrics={})


def test_registry_refreshes_after_ttl(monkeypatch):
    monkeypatch.setenv("REGISTRY_REFRESH_SECONDS", "0")
    signals_side = ModelRegistry()          # long-lived process
    other = ModelRegistry()                 # another instance
    _reg_model(other, "v1")
    other.promote_to_champion(f"{SYM}_v1")
    assert signals_side.get_champion(SYM).model_id == f"{SYM}_v1"


def test_registry_respects_ttl(monkeypatch):
    monkeypatch.setenv("REGISTRY_REFRESH_SECONDS", "3600")
    signals_side = ModelRegistry()
    other = ModelRegistry()
    _reg_model(other, "v1")
    other.promote_to_champion(f"{SYM}_v1")
    assert signals_side.get_champion(SYM) is None  # within TTL: snapshot


def test_refresh_failure_keeps_snapshot(monkeypatch):
    monkeypatch.setenv("REGISTRY_REFRESH_SECONDS", "0")
    reg = ModelRegistry()
    _reg_model(reg, "v1")
    reg.promote_to_champion(f"{SYM}_v1")
    monkeypatch.setattr(reg, "_get_firestore", lambda: SimpleNamespace(
        collection=lambda n: (_ for _ in ()).throw(RuntimeError("down"))))
    assert reg.get_champion(SYM).model_id == f"{SYM}_v1"


def test_cache_only_serves_matching_model():
    cache_mod.cache_model(SYM, "old", path="runs\\v1\\m.joblib", version="v1")
    assert cache_mod.get_cached_model(SYM, expected_path="runs/v1/m.joblib") == "old"
    assert cache_mod.get_cached_model(SYM, expected_path="runs/v2/m.joblib") is None
    assert cache_mod.get_cached_model(SYM) == "old"


def _model(direction):
    p = ModelPrediction(0.9, 0.1, 0.05, 0.9, 0.1) if direction == "long" else ModelPrediction(0.1, 0.9, -0.05, 0.9, 0.1)
    return SimpleNamespace(predict_single=lambda x: p)


def _signals(monkeypatch, champion_version, load):
    from tests import signal_harness as h
    comps = h.install(monkeypatch)
    cache_mod.clear_cache()
    cache_mod.cache_model(SYM, _model("long"), path="runs/v1/m.joblib", version="v1")
    comps.model_registry = SimpleNamespace(
        get_champion=lambda s: SimpleNamespace(version=champion_version,
                                               xgboost_path=f"runs/{champion_version}/m.joblib"),
        list_models=lambda **k: [],
    )
    import app.models.xgboost_model as xm
    monkeypatch.setattr(xm.XGBoostDecisionModel, "load", staticmethod(load))
    get_state_manager().override(SYM, S.PROMOTED, "t", "seed")
    return h


def test_new_champion_is_loaded_not_stale_cache(monkeypatch):
    """Old bug: cache keyed by symbol served v1 under v2's label."""
    h = _signals(monkeypatch, "v2", lambda p: _model("short"))
    data = h.get()["data"]
    assert data["signal"] == "short" and data["metadata"]["model_version"] == "v2"
    assert cache_mod.get_cached_entry(SYM).version == "v2"


def test_new_champion_unloadable_serves_old_with_true_label(monkeypatch):
    def boom(p):
        raise FileNotFoundError("not in GCS yet")

    h = _signals(monkeypatch, "v2", boom)
    data = h.get()["data"]
    assert data["signal"] == "long" and data["metadata"]["model_version"] == "v1"


def test_same_champion_uses_cache(monkeypatch):
    def boom(p):
        raise AssertionError("must not reload")

    h = _signals(monkeypatch, "v1", boom)
    assert h.get()["data"]["metadata"]["model_version"] == "v1"
