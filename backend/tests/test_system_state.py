"""SystemState transition/permission tables and the training flow that drives them."""
import itertools

import pytest

from app.core.system_state import (
    SystemState as S,
    VALID_TRANSITIONS,
    SERVICE_PERMISSIONS,
    DEPRECATED_STATES,
    get_state_manager,
    resume_state_after_training,
)

SYM = "BTCUSDT"


def _seed(state, metadata=None):
    mgr = get_state_manager()
    mgr.override(SYM, state, actor="test", reason="seed", metadata=metadata)
    return mgr


VALID_PAIRS = [(a, b) for a, targets in VALID_TRANSITIONS.items() for b in targets]
ILLEGAL_PAIRS = [
    (a, b) for a, b in itertools.product(S, S)
    if b not in VALID_TRANSITIONS.get(a, set())
]


# ── table-level invariants ──────────────────────────────────────────

def test_every_state_has_an_exit():
    for state in S:
        assert VALID_TRANSITIONS.get(state), f"{state} is a dead end"


def test_training_permission_consistent_with_transition_table():
    for state in SERVICE_PERMISSIONS["training"]:
        assert S.TRAINING in VALID_TRANSITIONS[state], (
            f"{state} permits training but TRAINING is not a valid transition"
        )


def test_scheduled_training_allowed_from_serving_states():
    assert S.PROMOTED in SERVICE_PERMISSIONS["training"]
    assert S.LIVE_DEGRADED in SERVICE_PERMISSIONS["training"]
    assert S.TRAINING_FAILED in SERVICE_PERMISSIONS["training"]


def test_deprecated_states_are_never_entered():
    for state, targets in VALID_TRANSITIONS.items():
        assert not (targets & DEPRECATED_STATES), f"{state} can enter a deprecated state"


def test_challenger_ready_has_exits_including_training():
    assert S.TRAINING in VALID_TRANSITIONS[S.CHALLENGER_READY]
    assert {S.PROMOTED, S.REJECTED, S.FORWARD_TESTING} <= VALID_TRANSITIONS[S.CHALLENGER_READY]


def test_forward_testing_and_rejected_are_wired():
    assert S.FORWARD_TESTING in VALID_TRANSITIONS[S.TRAINING]
    assert S.REJECTED in VALID_TRANSITIONS[S.TRAINING]
    assert S.PROMOTED in VALID_TRANSITIONS[S.FORWARD_TESTING]


def test_insufficient_data_transition_is_valid():
    assert S.COLLECTING_DATA in VALID_TRANSITIONS[S.TRAINING]


def test_signal_generation_allowed_while_retraining_a_champion():
    for state in (S.PROMOTED, S.LIVE_DEGRADED, S.TRAINING, S.TRAINING_FAILED):
        assert state in SERVICE_PERMISSIONS["signal_generation"]


# ── every valid transition / illegal transitions ────────────────────

@pytest.mark.parametrize("src,dst", VALID_PAIRS, ids=lambda s: s.value)
def test_valid_transition(src, dst):
    mgr = _seed(src)
    assert mgr.transition(SYM, dst, actor="test", reason="t") is True
    assert mgr.get_state(SYM).state == dst


@pytest.mark.parametrize("src,dst", ILLEGAL_PAIRS, ids=lambda s: s.value)
def test_illegal_transition_rejected(src, dst):
    mgr = _seed(src)
    assert mgr.transition(SYM, dst, actor="test", reason="t") is False
    assert mgr.get_state(SYM).state == src


def test_initial_state_is_no_data():
    assert get_state_manager().get_state(SYM).state == S.NO_DATA


# ── force / override ────────────────────────────────────────────────

def test_hardcoded_force_key_no_longer_accepted():
    mgr = get_state_manager()
    assert mgr.force_state(SYM, S.PROMOTED, "ADMIN_FORCE_KEY", "x") is False
    assert mgr.get_state(SYM).state == S.NO_DATA


def test_force_state_with_env_key_is_audited(fake_db, monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET_KEY", "k")
    mgr = get_state_manager()
    assert mgr.force_state(SYM, S.READY_FOR_TRAINING, "k", "recovery") is True
    assert mgr.get_state(SYM).state == S.READY_FOR_TRAINING
    logs = [d.to_dict() for d in fake_db.collection("state_transitions").stream()]
    assert any(l["to_state"] == "ready_for_training" and l["actor"] == "admin_force" for l in logs)


def test_resume_state_after_training():
    from app.core.system_state import StateSnapshot
    from datetime import datetime
    snap = lambda st, md=None: StateSnapshot(st, SYM, datetime.utcnow(), "t", "r", md or {})
    assert resume_state_after_training(None) == S.PROMOTED
    assert resume_state_after_training(snap(S.PROMOTED)) == S.PROMOTED
    assert resume_state_after_training(snap(S.LIVE_DEGRADED)) == S.LIVE_DEGRADED
    assert resume_state_after_training(
        snap(S.TRAINING_FAILED, {"resume_state": "live_degraded"})
    ) == S.LIVE_DEGRADED


# ── training flow (integration) ─────────────────────────────────────

@pytest.fixture
def harness(monkeypatch, tmp_path):
    from tests import training_harness as h
    cfg = h.install(monkeypatch, tmp_path)
    return h, cfg


def test_scheduled_training_from_ready_promotes_first_model(harness):
    h, _ = harness
    _seed(S.READY_FOR_TRAINING)
    res = h.run([SYM])
    assert res[SYM]["status"] == "completed" and res[SYM]["promoted"] is True
    assert get_state_manager().get_state(SYM).state == S.PROMOTED


def test_scheduled_retrain_from_promoted_without_force(harness):
    h, cfg = harness
    _seed(S.READY_FOR_TRAINING)
    h.run([SYM])  # first champion
    cfg.baseline_sharpe = 100.0  # challenger cannot beat baselines
    res = h.run([SYM])  # scheduled, force=False
    assert res[SYM]["status"] == "completed" and res[SYM]["promoted"] is False
    # Champion still serving -> back to PROMOTED, not stuck in CHALLENGER_READY
    assert get_state_manager().get_state(SYM).state == S.PROMOTED


def test_retrain_from_live_degraded_stays_degraded_when_not_promoted(harness):
    h, cfg = harness
    _seed(S.READY_FOR_TRAINING)
    h.run([SYM])
    get_state_manager().transition(SYM, S.LIVE_DEGRADED, "test", "degraded")
    cfg.baseline_sharpe = 100.0
    h.run([SYM])
    assert get_state_manager().get_state(SYM).state == S.LIVE_DEGRADED


def test_insufficient_data_goes_to_collecting_data(harness):
    h, cfg = harness
    _seed(S.READY_FOR_TRAINING)
    cfg.insufficient_data = True
    res = h.run([SYM])
    assert res[SYM]["reason"] == "insufficient_data"
    assert get_state_manager().get_state(SYM).state == S.COLLECTING_DATA


def test_insufficient_data_with_champion_keeps_serving(harness):
    h, cfg = harness
    _seed(S.READY_FOR_TRAINING)
    h.run([SYM])
    cfg.insufficient_data = True
    h.run([SYM])
    assert get_state_manager().get_state(SYM).state == S.PROMOTED


def test_training_failure_then_scheduled_retry(harness):
    h, cfg = harness
    _seed(S.READY_FOR_TRAINING)
    cfg.train_raises = RuntimeError("boom")
    assert h.run([SYM])[SYM]["status"] == "failed"
    assert get_state_manager().get_state(SYM).state == S.TRAINING_FAILED
    cfg.train_raises = None
    assert h.run([SYM])[SYM]["status"] == "completed"  # no force needed
    assert get_state_manager().get_state(SYM).state == S.PROMOTED


def test_legacy_challenger_ready_is_unstuck(harness):
    h, _ = harness
    _seed(S.CHALLENGER_READY)
    assert h.run([SYM])[SYM]["status"] == "completed"


def test_scheduled_training_skipped_in_no_data(harness):
    h, _ = harness
    res = h.run([SYM])
    assert res[SYM] == {"status": "skipped", "reason": "invalid_state:no_data"}


def test_force_from_non_training_state_uses_audited_override(harness, fake_db):
    h, _ = harness
    res = h.run([SYM], force=True)  # from NO_DATA
    assert res[SYM]["status"] == "completed"
    logs = [d.to_dict() for d in fake_db.collection("state_transitions").stream()]
    assert any(l["actor"] == "training_api_force" and l["to_state"] == "training" for l in logs)
