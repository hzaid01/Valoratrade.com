"""Job lock state machine: every valid transition plus the illegal ones."""
from datetime import datetime, timedelta

import pytest

from app.core.job_lock import JobLockManager, JobOutcome, LockDenial


@pytest.fixture
def mgr():
    return JobLockManager()


def _retries(db, doc="BTCUSDT_training"):
    data = db.raw("job_retries", doc)
    return (data or {}).get("count", 0)


# ── acquire ──────────────────────────────────────────────────────────

def test_acquire_unlocked_creates_lock(mgr, fake_db):
    lock = mgr.acquire("BTCUSDT", "training", "test")
    assert lock is not None
    doc = fake_db.raw("job_locks", "BTCUSDT_training")
    assert doc["locked"] is True and doc["job_id"] == lock.job_id
    assert fake_db.transaction_count >= 1  # went through a transaction


def test_acquire_held_lock_is_denied(mgr):
    assert mgr.acquire("BTCUSDT", "training")
    res = mgr.try_acquire("BTCUSDT", "training")
    assert res.lock is None and res.denial == LockDenial.HELD


def test_force_does_not_bypass_a_live_lock(mgr):
    assert mgr.acquire("BTCUSDT", "training")
    res = mgr.try_acquire("BTCUSDT", "training", ignore_retry_limit=True)
    assert res.lock is None and res.denial == LockDenial.HELD


def test_stale_lock_is_taken_over_and_orphan_logged(mgr, fake_db):
    old = mgr.acquire("BTCUSDT", "training")
    stale = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    fake_db.collection("job_locks").document("BTCUSDT_training").update({"heartbeat": stale})

    res = mgr.try_acquire("BTCUSDT", "training")
    assert res.lock is not None and res.lock.job_id != old.job_id
    assert res.orphan["job_id"] == old.job_id
    orphans = fake_db.collection("orphan_jobs").stream()
    assert [o.to_dict()["job_id"] for o in orphans] == [old.job_id]


def test_malformed_lock_treated_as_stale(mgr, fake_db):
    fake_db.collection("job_locks").document("BTCUSDT_training").set({"locked": True, "job_id": "x"})
    assert mgr.acquire("BTCUSDT", "training") is not None


def test_acquire_after_release_succeeds(mgr):
    lock = mgr.acquire("BTCUSDT", "training")
    mgr.release(lock, JobOutcome.SUCCESS)
    assert mgr.acquire("BTCUSDT", "training") is not None


# ── release outcomes / retry accounting ─────────────────────────────

def test_release_success_resets_retries(mgr, fake_db):
    fake_db.collection("job_retries").document("BTCUSDT_training").set({"count": 2})
    lock = mgr.acquire("BTCUSDT", "training")
    assert mgr.release(lock, JobOutcome.SUCCESS) is True
    assert _retries(fake_db) == 0
    assert fake_db.raw("job_locks", "BTCUSDT_training")["locked"] is False


def test_release_skipped_does_not_touch_retries(mgr, fake_db):
    fake_db.collection("job_retries").document("BTCUSDT_training").set({"count": 2})
    lock = mgr.acquire("BTCUSDT", "training")
    assert mgr.release(lock, JobOutcome.SKIPPED) is True
    assert _retries(fake_db) == 2
    doc = fake_db.raw("job_locks", "BTCUSDT_training")
    assert doc["locked"] is False and doc["last_outcome"] == "skipped"


def test_release_failed_increments_retries(mgr, fake_db):
    lock = mgr.acquire("BTCUSDT", "training")
    mgr.release(lock, JobOutcome.FAILED)
    assert _retries(fake_db) == 1


def test_release_bool_backward_compatible(mgr, fake_db):
    lock = mgr.acquire("BTCUSDT", "training")
    mgr.release(lock, False)
    assert _retries(fake_db) == 1
    lock = mgr.acquire("BTCUSDT", "training")
    mgr.release(lock, True)
    assert _retries(fake_db) == 0


def test_many_skips_never_exhaust_retries(mgr, fake_db):
    for _ in range(10):
        lock = mgr.acquire("BTCUSDT", "training")
        assert lock is not None
        mgr.release(lock, JobOutcome.SKIPPED)
    assert _retries(fake_db) == 0


def test_exhausted_retries_deny_unless_forced(mgr, fake_db):
    for _ in range(JobLockManager.MAX_RETRIES):
        mgr.release(mgr.acquire("BTCUSDT", "training"), JobOutcome.FAILED)
    res = mgr.try_acquire("BTCUSDT", "training")
    assert res.lock is None and res.denial == LockDenial.MAX_RETRIES

    forced = mgr.try_acquire("BTCUSDT", "training", ignore_retry_limit=True)
    assert forced.lock is not None and forced.retry_limit_bypassed is True
    mgr.release(forced.lock, JobOutcome.SUCCESS)
    assert mgr.acquire("BTCUSDT", "training") is not None


# ── illegal transitions ─────────────────────────────────────────────

def test_release_by_non_owner_is_rejected(mgr, fake_db):
    old = mgr.acquire("BTCUSDT", "training")
    stale = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    fake_db.collection("job_locks").document("BTCUSDT_training").update({"heartbeat": stale})
    new = mgr.acquire("BTCUSDT", "training")

    assert mgr.release(old, JobOutcome.FAILED) is False  # old owner can't clobber
    doc = fake_db.raw("job_locks", "BTCUSDT_training")
    assert doc["locked"] is True and doc["job_id"] == new.job_id
    assert _retries(fake_db) == 0


def test_double_release_is_rejected_and_idempotent(mgr, fake_db):
    lock = mgr.acquire("BTCUSDT", "training")
    assert mgr.release(lock, JobOutcome.FAILED) is True
    assert mgr.release(lock, JobOutcome.FAILED) is False
    assert _retries(fake_db) == 1  # duplicate release did not double-count


def test_heartbeat_after_takeover_reports_lost(mgr, fake_db):
    old = mgr.acquire("BTCUSDT", "training")
    assert mgr.heartbeat(old) is True
    stale = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    fake_db.collection("job_locks").document("BTCUSDT_training").update({"heartbeat": stale})
    mgr.acquire("BTCUSDT", "training")
    assert mgr.heartbeat(old) is False


def test_is_locked(mgr):
    assert mgr.is_locked("BTCUSDT", "training") is False
    lock = mgr.acquire("BTCUSDT", "training")
    assert mgr.is_locked("BTCUSDT", "training") is True
    mgr.release(lock)
    assert mgr.is_locked("BTCUSDT", "training") is False


# ── training-job integration ────────────────────────────────────────

def test_job_outcome_mapping():
    from app.api.training import _job_outcome
    assert _job_outcome("completed") == JobOutcome.SUCCESS
    assert _job_outcome("skipped") == JobOutcome.SKIPPED
    assert _job_outcome("failed") == JobOutcome.FAILED
    assert _job_outcome("invalid") == JobOutcome.FAILED
    assert _job_outcome(None) == JobOutcome.FAILED


def test_training_skip_for_invalid_state_does_not_count(monkeypatch, tmp_path, fake_db):
    from tests import training_harness as h
    h.install(monkeypatch, tmp_path)
    # NO_DATA is not a training state -> skipped
    for _ in range(5):
        res = h.run(["BTCUSDT"])
        assert res["BTCUSDT"]["status"] == "skipped"
    assert _retries(fake_db) == 0
    assert fake_db.raw("job_locks", "BTCUSDT_training")["locked"] is False


def test_training_reports_max_retries_and_force_bypasses(monkeypatch, tmp_path, fake_db):
    from tests import training_harness as h
    h.install(monkeypatch, tmp_path)
    fake_db.collection("job_retries").document("BTCUSDT_training").set({"count": 3})

    res = h.run(["BTCUSDT"])
    assert res["BTCUSDT"] == {"status": "skipped", "reason": "max_retries_exceeded"}

    res = h.run(["BTCUSDT"], force=True)
    assert res["BTCUSDT"]["status"] == "completed"
    assert _retries(fake_db) == 0


def test_reset_script_dry_run_and_apply(fake_db):
    import importlib.util, os
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "reset_job_retries.py")
    spec = importlib.util.spec_from_file_location("reset_job_retries", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    col = fake_db.collection("job_retries")
    col.document("BTCUSDT_training").set({"count": 3})
    col.document("ETHUSDT_training").set({"count": 1})
    col.document("SOLUSDT_training").set({"count": 0})

    dry = mod.reset_job_retries(fake_db)
    assert {c["doc_id"] for c in dry} == {"BTCUSDT_training", "ETHUSDT_training"}
    assert fake_db.raw("job_retries", "BTCUSDT_training")["count"] == 3  # untouched

    applied = mod.reset_job_retries(fake_db, symbol="BTCUSDT", apply=True)
    assert [c["doc_id"] for c in applied] == ["BTCUSDT_training"]
    assert fake_db.raw("job_retries", "BTCUSDT_training")["count"] == 0
    assert fake_db.raw("job_retries", "ETHUSDT_training")["count"] == 1
