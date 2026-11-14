"""Signal gating: hard kill-switch block + SIGNAL_STATE_GATE log_only|enforce."""
import pytest

from app.core.system_state import SystemState as S, get_state_manager
from tests import signal_harness as h

SYM = "BTCUSDT"


def _state(state):
    get_state_manager().override(SYM, state, "test", "seed")


def test_baseline_signal_is_emitted(monkeypatch):
    h.install(monkeypatch)
    _state(S.PROMOTED)
    data = h.get()["data"]
    assert data["signal"] == "long"
    assert data["trade"]["position_size"] == 0.5


def test_kill_switch_hard_blocks_signal(monkeypatch):
    comps = h.install(monkeypatch)
    _state(S.PROMOTED)
    comps.capital_controller._trigger_kill("test")
    data = h.get()["data"]
    assert data["signal"] == "no_trade"
    assert data["metadata"]["filters_passed"]["rejection_reason"] == "kill_switch_active"
    assert data["trade"]["position_size"] is None
    assert "indicators" in data  # response shape unchanged


def test_kill_switch_blocks_even_in_log_only(monkeypatch):
    monkeypatch.setenv("SIGNAL_STATE_GATE", "log_only")
    comps = h.install(monkeypatch)
    _state(S.PROMOTED)
    comps.capital_controller._trigger_kill("test")
    assert h.get()["data"]["signal"] == "no_trade"


def test_kill_switch_blocks_before_other_filters(monkeypatch):
    """Even if the regime filter would fire first, the reason is the kill switch."""
    from app.models.xgboost_model import ModelPrediction
    comps = h.install(monkeypatch, prediction=ModelPrediction(0.5, 0.5, 0.0, 0.1, 0.9))
    comps.capital_controller._trigger_kill("test")
    _state(S.PROMOTED)
    data = h.get()["data"]
    assert data["metadata"]["filters_passed"]["rejection_reason"] == "kill_switch_active"


def test_state_gate_log_only_is_default_and_does_not_block(monkeypatch, caplog):
    monkeypatch.delenv("SIGNAL_STATE_GATE", raising=False)
    h.install(monkeypatch)
    _state(S.COLLECTING_DATA)  # not permitted for signal_generation
    with caplog.at_level("WARNING"):
        data = h.get()["data"]
    assert data["signal"] == "long"
    assert "WOULD HAVE BLOCKED" in caplog.text


def test_state_gate_enforce_blocks(monkeypatch):
    monkeypatch.setenv("SIGNAL_STATE_GATE", "enforce")
    h.install(monkeypatch)
    _state(S.COLLECTING_DATA)
    data = h.get()["data"]
    assert data["signal"] == "system_not_ready"
    assert "collecting_data" in data["metadata"]["system_status"]
    assert "indicators" in data


def test_state_gate_enforce_allows_permitted_states(monkeypatch):
    monkeypatch.setenv("SIGNAL_STATE_GATE", "enforce")
    h.install(monkeypatch)
    for st in (S.PROMOTED, S.LIVE_DEGRADED, S.TRAINING):
        _state(st)
        assert h.get()["data"]["signal"] == "long", st


def test_state_gate_unknown_value_falls_back_to_log_only(monkeypatch):
    monkeypatch.setenv("SIGNAL_STATE_GATE", "enforc")  # typo
    h.install(monkeypatch)
    _state(S.COLLECTING_DATA)
    assert h.get()["data"]["signal"] == "long"


def test_state_read_failure_log_only_allows_enforce_blocks(monkeypatch):
    from app.core.system_state import SystemStateManager

    def boom(self, *a, **k):
        raise RuntimeError("firestore down")

    h.install(monkeypatch)
    monkeypatch.setattr(SystemStateManager, "can_run_service", boom)
    monkeypatch.setenv("SIGNAL_STATE_GATE", "log_only")
    assert h.get()["data"]["signal"] == "long"
    monkeypatch.setenv("SIGNAL_STATE_GATE", "enforce")
    assert h.get()["data"]["signal"] == "system_not_ready"


def test_no_champion_still_returns_system_not_ready(monkeypatch):
    h.install(monkeypatch, champion=False)
    assert h.get()["data"]["signal"] == "system_not_ready"
