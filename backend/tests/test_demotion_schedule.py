"""Champion demotion runs from the forward cycle, behind DEMOTION_MODE."""
from types import SimpleNamespace

import pytest

from app.core.system_state import SystemState as S, get_state_manager
from app.evaluation.forward_cycle import run_forward_cycle
from app.models.registry.champion_challenger import ChampionChallenger
from app.models.registry.demotion import ModelDemotion
from app.models.registry.model_registry import ModelRegistry

SYM = "BTCUSDT"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIN_FORWARD_SAMPLES", "5")


def _register(reg, v, acc=0.6):
    reg.register(version=v, symbol=SYM, patch_tst_path="", xgboost_path=f"x_{v}",
                 dataset_version="d", feature_version="f", training_snapshot="s",
                 training_metrics={}, validation_metrics={"accuracy": acc, "sharpe_ratio": 1.0})


class FakeForward:
    def __init__(self, acc_by_version, n=10):
        self.acc, self.n = acc_by_version, n

    def resolve_pending(self, price_at):
        return []

    def get_forward_metrics(self, model_version=None, days=7):
        acc = self.acc.get(model_version)
        n = self.n if acc is not None else 0
        return SimpleNamespace(resolved_predictions=n,
                               to_dict=lambda: {"accuracy": acc or 0.0, "resolved_predictions": n})


def _cycle(reg, fwd, promoted=None):
    return run_forward_cycle(
        [SYM], fwd, reg, ChampionChallenger(reg, SimpleNamespace()), get_state_manager(),
        price_at=lambda s, t: 100.0,
        on_promoted=(lambda s, m: promoted.append(m.model_id)) if promoted is not None else None,
    )


def test_missing_metrics_never_demote():
    """Old bug: a missing rolling_accuracy was read as 0 -> 100% degraded."""
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    d = ModelDemotion(registry=reg)
    assert d.evaluate(SYM, {}) is None
    assert d.evaluate(SYM, {"accuracy": 0.6}) is None
    assert d.check_degradation(SYM, {}) is None
    assert reg.get_champion(SYM).model_id == f"{SYM}_champ"


def test_evaluate_reasons():
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    d = ModelDemotion(registry=reg)
    assert "Accuracy degraded" in d.evaluate(SYM, {"rolling_accuracy": 0.3})
    assert d.evaluate(SYM, {"rolling_accuracy": 0.55}) is None
    assert "Drawdown" in d.evaluate(SYM, {"max_drawdown": 0.5})
    assert "consecutive" in d.evaluate(SYM, {"consecutive_failures": 3})


def test_log_only_reports_but_keeps_champion(monkeypatch, caplog):
    monkeypatch.delenv("DEMOTION_MODE", raising=False)
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    with caplog.at_level("WARNING"):
        summary = _cycle(reg, FakeForward({"champ": 0.2}))
    [d] = summary["demotions"]
    assert d["model_id"] == f"{SYM}_champ" and d["applied"] is False and "Accuracy degraded" in d["reason"]
    assert "DEMOTION_MODE log_only: WOULD HAVE DEMOTED" in caplog.text
    assert ModelRegistry().get_champion(SYM).model_id == f"{SYM}_champ"


def test_enforce_rolls_back_to_superseded_champion(monkeypatch):
    monkeypatch.setenv("DEMOTION_MODE", "enforce")
    reg = ModelRegistry()
    _register(reg, "old"); _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_old")
    reg.promote_to_champion(f"{SYM}_champ")
    promoted = []
    summary = _cycle(reg, FakeForward({"champ": 0.2}), promoted)
    assert summary["demotions"][0]["applied"] is True
    assert summary["demotions"][0]["rolled_back_to"] == f"{SYM}_old"
    fresh = ModelRegistry()
    assert fresh.get_champion(SYM).model_id == f"{SYM}_old"
    assert fresh.get(f"{SYM}_champ").retired_reason == "demoted"
    assert promoted == [f"{SYM}_old"]  # model cache refreshed for the rollback target


def test_enforce_without_rollback_goes_degraded(monkeypatch):
    monkeypatch.setenv("DEMOTION_MODE", "enforce")
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    get_state_manager().override(SYM, S.PROMOTED, "t", "seed")
    _cycle(reg, FakeForward({"champ": 0.2}))
    assert get_state_manager().get_state(SYM).state == S.LIVE_DEGRADED


def test_healthy_champion_untouched(monkeypatch):
    monkeypatch.setenv("DEMOTION_MODE", "enforce")
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    assert _cycle(reg, FakeForward({"champ": 0.58}))["demotions"] == []


def test_too_few_samples_no_demotion(monkeypatch):
    monkeypatch.setenv("DEMOTION_MODE", "enforce")
    reg = ModelRegistry()
    _register(reg, "champ")
    reg.promote_to_champion(f"{SYM}_champ")
    assert _cycle(reg, FakeForward({"champ": 0.1}, n=2))["demotions"] == []
