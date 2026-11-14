"""transition() atomicity, no stale cache, and call sites handling False."""
import threading

import pytest

from app.core.system_state import SystemState as S, SystemStateManager, get_state_manager

SYM = "BTCUSDT"


def test_transition_runs_in_a_transaction(fake_db):
    mgr = SystemStateManager()
    before = fake_db.transaction_count
    assert mgr.transition(SYM, S.COLLECTING_DATA, "t", "r")
    assert fake_db.transaction_count > before


def test_audit_entry_written_with_transition_and_not_on_rejection(fake_db):
    mgr = SystemStateManager()
    mgr.transition(SYM, S.COLLECTING_DATA, "t", "r")
    mgr.transition(SYM, S.PROMOTED, "t", "illegal")  # COLLECTING_DATA -> PROMOTED
    logs = [d.to_dict() for d in fake_db.collection("state_transitions").stream()]
    assert [(l["from_state"], l["to_state"]) for l in logs] == [("no_data", "collecting_data")]


def test_no_stale_cache_across_instances():
    a, b = SystemStateManager(), SystemStateManager()  # two Cloud Run instances
    assert a.get_state(SYM).state == S.NO_DATA
    assert b.transition(SYM, S.COLLECTING_DATA, "b", "r")
    # Instance A sees B's write immediately (old code cached for 30s).
    assert a.get_state(SYM).state == S.COLLECTING_DATA


def test_two_instances_cannot_double_transition():
    a, b = SystemStateManager(), SystemStateManager()
    a.override(SYM, S.READY_FOR_TRAINING, "seed", "seed")
    a.get_state(SYM); b.get_state(SYM)  # both have "seen" READY
    assert a.transition(SYM, S.TRAINING, "a", "start") is True
    assert b.transition(SYM, S.TRAINING, "b", "start") is False


def test_concurrent_transitions_exactly_one_wins():
    SystemStateManager().override(SYM, S.READY_FOR_TRAINING, "seed", "seed")
    results = []
    barrier = threading.Barrier(8)

    def worker():
        mgr = SystemStateManager()
        barrier.wait()
        results.append(mgr.transition(SYM, S.TRAINING, "w", "start"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1


def test_get_state_initialisation_does_not_clobber_existing(fake_db):
    mgr = SystemStateManager()
    mgr.override(SYM, S.PROMOTED, "seed", "seed")
    assert SystemStateManager().get_state(SYM).state == S.PROMOTED
    assert fake_db.raw("system_state", SYM)["state"] == "promoted"


def test_transition_validates_against_stored_state_not_caller_view(fake_db):
    mgr = SystemStateManager()
    mgr.override(SYM, S.PROMOTED, "seed", "seed")
    # Out-of-band write (another instance) moves it to TRAINING.
    doc = fake_db.raw("system_state", SYM)
    doc["state"] = "training"
    fake_db.collection("system_state").document(SYM).set(doc)
    assert mgr.transition(SYM, S.TRAINING, "t", "r") is False  # TRAINING->TRAINING illegal


# ── call sites ──────────────────────────────────────────────────────

def test_training_skips_when_start_transition_rejected(monkeypatch, tmp_path, fake_db):
    from tests import training_harness as h
    h.install(monkeypatch, tmp_path)
    get_state_manager().override(SYM, S.READY_FOR_TRAINING, "seed", "seed")

    real = SystemStateManager.transition

    def reject_training(self, symbol, new_state, *a, **k):
        if new_state == S.TRAINING:
            return False
        return real(self, symbol, new_state, *a, **k)

    monkeypatch.setattr(SystemStateManager, "transition", reject_training)
    res = h.run([SYM])
    assert res[SYM] == {"status": "skipped", "reason": "state_transition_rejected"}
    # skipped -> retry counter untouched, lock released
    assert (fake_db.raw("job_retries", "BTCUSDT_training") or {}).get("count", 0) == 0
    assert fake_db.raw("job_locks", "BTCUSDT_training")["locked"] is False


def test_end_of_run_rejection_is_logged(monkeypatch, tmp_path, caplog):
    from tests import training_harness as h
    h.install(monkeypatch, tmp_path)
    get_state_manager().override(SYM, S.READY_FOR_TRAINING, "seed", "seed")

    real = SystemStateManager.transition

    def reject_promoted(self, symbol, new_state, *a, **k):
        if new_state == S.PROMOTED:
            return False
        return real(self, symbol, new_state, *a, **k)

    monkeypatch.setattr(SystemStateManager, "transition", reject_promoted)
    with caplog.at_level("ERROR"):
        h.run([SYM])
    assert "STATE TRANSITION REJECTED: wanted promoted" in caplog.text


def test_data_store_logs_rejected_transition(monkeypatch, caplog):
    from app.core.data_store import DataStore
    ds = DataStore()
    monkeypatch.setattr(SystemStateManager, "transition", lambda self, *a, **k: False)
    with caplog.at_level("WARNING"):
        ds._update_state_for_data(SYM, 10)
    assert "transition to collecting_data rejected" in caplog.text


def test_data_store_happy_path():
    from app.core.data_store import DataStore
    ds = DataStore()
    ds._update_state_for_data(SYM, 10)
    assert get_state_manager().get_state(SYM).state == S.COLLECTING_DATA
    ds._update_state_for_data(SYM, 600)
    assert get_state_manager().get_state(SYM).state == S.READY_FOR_TRAINING
