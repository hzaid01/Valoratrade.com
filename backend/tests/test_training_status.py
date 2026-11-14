"""Training cooldown / last_success only after a run that trained something."""
import pytest

from app.core.system_state import SystemState as S, get_state_manager


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from tests import training_harness as h
    import app.api.training as training
    cfg = h.install(monkeypatch, tmp_path)
    for k, v in {"last_success": None, "cooldown_until": None, "run_count": 0, "is_running": False}.items():
        monkeypatch.setitem(training._training_status, k, v)
    return h, cfg, training


def test_failed_run_sets_no_cooldown(harness):
    h, cfg, training = harness
    get_state_manager().override("BTCUSDT", S.READY_FOR_TRAINING, "t", "seed")
    cfg.train_raises = RuntimeError("boom")
    h.run(["BTCUSDT"])
    st = training._training_status
    assert st["last_success"] is None and st["cooldown_until"] is None
    assert st["run_count"] == 1 and st["is_running"] is False


def test_skipped_run_sets_no_cooldown(harness):
    h, _, training = harness
    h.run(["BTCUSDT"])  # NO_DATA -> skipped
    assert training._training_status["cooldown_until"] is None


def test_completed_run_sets_cooldown(harness):
    h, _, training = harness
    get_state_manager().override("BTCUSDT", S.READY_FOR_TRAINING, "t", "seed")
    h.run(["BTCUSDT"])
    st = training._training_status
    assert st["last_success"] is not None and st["cooldown_until"] is not None
