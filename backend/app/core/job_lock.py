"""
Job Lock & Concurrency Control

Prevents concurrent training jobs and handles orphan cleanup.

Lock lifecycle (one Firestore doc per ``{symbol}_{job_type}`` in ``job_locks``):

    unlocked --acquire--> locked(job_id) --release(outcome)--> unlocked

* acquire is a single Firestore transaction: read lock + retry count, decide,
  write the new lock. Two instances can no longer both win.
* A lock whose heartbeat is older than HEARTBEAT_TIMEOUT is stale and may be
  taken over (the orphan is logged to ``orphan_jobs``).
* release/heartbeat only act if the caller still owns the lock (job_id match),
  so a job whose stale lock was taken over cannot clobber the new owner.
* Retry accounting (``job_retries``): only JobOutcome.FAILED increments the
  count. SKIPPED (invalid state, insufficient data, cooldown, ...) leaves it
  unchanged; SUCCESS resets it. ``ignore_retry_limit=True`` (force runs)
  bypasses an exhausted retry count.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Union
import uuid

from app.firebase_config import get_firestore
from app.core.fs_tx import run_transaction

logger = logging.getLogger(__name__)


class JobOutcome(Enum):
    """How a job that held a lock ended."""
    SUCCESS = "success"   # resets retry count
    SKIPPED = "skipped"   # did no work by design; retry count unchanged
    FAILED = "failed"     # real failure; retry count + 1


class LockDenial(Enum):
    """Why acquire() did not return a lock."""
    HELD = "locked_by_another_job"
    MAX_RETRIES = "max_retries_exceeded"


@dataclass
class JobLock:
    """Represents a job lock."""
    job_id: str
    symbol: str
    job_type: str  # "training", "ingestion"
    locked_at: datetime
    heartbeat: datetime
    owner: str

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "symbol": self.symbol,
            "job_type": self.job_type,
            "locked_at": self.locked_at.isoformat(),
            "heartbeat": self.heartbeat.isoformat(),
            "owner": self.owner,
            "locked": True
        }


@dataclass
class AcquireResult:
    lock: Optional[JobLock]
    denial: Optional[LockDenial] = None
    orphan: Optional[dict] = None  # stale lock that was taken over
    retry_limit_bypassed: bool = False


class JobLockManager:
    """
    Manages distributed job locks using Firestore.

    Enforces:
    - Only one training job per symbol
    - Stale lock detection (heartbeat > 10 min)
    - Orphan cleanup
    """

    HEARTBEAT_TIMEOUT = timedelta(minutes=10)
    MAX_RETRIES = 3

    def __init__(self):
        self.db = get_firestore()
        self.collection = self.db.collection('job_locks')
        self.retry_collection = self.db.collection('job_retries')

    @staticmethod
    def _doc_id(symbol: str, job_type: str) -> str:
        return f"{symbol}_{job_type}"

    def _is_stale(self, data: dict, now: datetime) -> bool:
        try:
            heartbeat = datetime.fromisoformat(data["heartbeat"])
        except (KeyError, TypeError, ValueError):
            return True  # malformed lock: treat as stale rather than deadlock
        return now - heartbeat > self.HEARTBEAT_TIMEOUT

    # ── acquire ────────────────────────────────────────────────────────

    def try_acquire(
        self,
        symbol: str,
        job_type: str,
        owner: str = "system",
        ignore_retry_limit: bool = False
    ) -> AcquireResult:
        """Atomically acquire the lock. Never raises for contention."""
        doc_id = self._doc_id(symbol, job_type)
        lock_ref = self.collection.document(doc_id)
        retry_ref = self.retry_collection.document(doc_id)

        def _tx(transaction) -> AcquireResult:
            now = datetime.utcnow()
            lock_snap = lock_ref.get(transaction=transaction)
            retry_snap = retry_ref.get(transaction=transaction)

            orphan = None
            if lock_snap.exists:
                data = lock_snap.to_dict() or {}
                if data.get("locked", False):
                    if not self._is_stale(data, now):
                        return AcquireResult(lock=None, denial=LockDenial.HELD)
                    orphan = data

            retry_count = (retry_snap.to_dict() or {}).get("count", 0) if retry_snap.exists else 0
            bypassed = False
            if retry_count >= self.MAX_RETRIES:
                if not ignore_retry_limit:
                    return AcquireResult(lock=None, denial=LockDenial.MAX_RETRIES)
                bypassed = True

            lock = JobLock(
                job_id=str(uuid.uuid4())[:8],
                symbol=symbol,
                job_type=job_type,
                locked_at=now,
                heartbeat=now,
                owner=owner
            )
            transaction.set(lock_ref, lock.to_dict())
            return AcquireResult(lock=lock, orphan=orphan, retry_limit_bypassed=bypassed)

        result = run_transaction(self.db, _tx)

        # Side effects outside the (retryable) transaction body.
        if result.orphan:
            logger.warning(
                f"Stale lock for {doc_id} taken over. "
                f"Orphan job {result.orphan.get('job_id')} logged."
            )
            self._log_orphan(result.orphan)
        if result.denial == LockDenial.HELD:
            logger.info(f"Lock {doc_id} held by another job")
        elif result.denial == LockDenial.MAX_RETRIES:
            logger.error(
                f"Max retries ({self.MAX_RETRIES}) exceeded for {doc_id}. "
                f"Use force=True or scripts/reset_job_retries.py."
            )
        if result.retry_limit_bypassed:
            logger.warning(f"Retry limit for {doc_id} bypassed (forced run)")
        if result.lock:
            logger.info(f"Acquired lock {doc_id} with job_id={result.lock.job_id}")
        return result

    def acquire(
        self,
        symbol: str,
        job_type: str,
        owner: str = "system",
        ignore_retry_limit: bool = False
    ) -> Optional[JobLock]:
        """
        Attempt to acquire a lock.

        Returns JobLock if acquired, None if locked by another or retries exhausted.
        """
        return self.try_acquire(symbol, job_type, owner, ignore_retry_limit).lock

    # ── release / heartbeat ────────────────────────────────────────────

    def release(self, lock: JobLock, success: Union[bool, JobOutcome] = True) -> bool:
        """
        Release a lock after job completion.

        ``success`` may be a JobOutcome, or a bool for backward compatibility
        (True -> SUCCESS, False -> FAILED). Returns False if the lock is no
        longer owned by this job (nothing is written in that case).
        """
        if isinstance(success, JobOutcome):
            outcome = success
        else:
            outcome = JobOutcome.SUCCESS if success else JobOutcome.FAILED

        doc_id = self._doc_id(lock.symbol, lock.job_type)
        lock_ref = self.collection.document(doc_id)
        retry_ref = self.retry_collection.document(doc_id)

        def _tx(transaction):
            now = datetime.utcnow().isoformat()
            snap = lock_ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else None
            if not data or not data.get("locked") or data.get("job_id") != lock.job_id:
                return False, None

            retry_snap = retry_ref.get(transaction=transaction)
            transaction.set(lock_ref, {
                "locked": False,
                "last_job_id": lock.job_id,
                "last_completed": now,
                "last_success": outcome == JobOutcome.SUCCESS,
                "last_outcome": outcome.value,
            })

            new_count = None
            if outcome == JobOutcome.SUCCESS:
                transaction.set(retry_ref, {"count": 0, "last_success": now})
                new_count = 0
            elif outcome == JobOutcome.FAILED:
                count = ((retry_snap.to_dict() or {}).get("count", 0) if retry_snap.exists else 0) + 1
                transaction.set(retry_ref, {"count": count, "last_failure": now})
                new_count = count
            # SKIPPED: retry doc untouched
            return True, new_count

        released, new_count = run_transaction(self.db, _tx)
        if not released:
            logger.error(
                f"Release of {doc_id} job_id={lock.job_id} ignored: "
                f"lock is no longer owned by this job (taken over or already released)"
            )
            return False
        if new_count is not None and new_count >= self.MAX_RETRIES:
            logger.error(f"Job {doc_id} has failed {new_count} times. Manual intervention required.")
        logger.info(f"Released lock {doc_id}, outcome={outcome.value}")
        return True

    def heartbeat(self, lock: JobLock) -> bool:
        """Update heartbeat. Returns False if this job no longer owns the lock."""
        doc_id = self._doc_id(lock.symbol, lock.job_type)
        lock_ref = self.collection.document(doc_id)

        def _tx(transaction):
            snap = lock_ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else None
            if not data or not data.get("locked") or data.get("job_id") != lock.job_id:
                return False
            transaction.update(lock_ref, {"heartbeat": datetime.utcnow().isoformat()})
            return True

        owned = run_transaction(self.db, _tx)
        if not owned:
            logger.error(f"Heartbeat for {doc_id} job_id={lock.job_id} rejected: lock lost")
        return owned

    def is_locked(self, symbol: str, job_type: str) -> bool:
        """Check if a lock is currently held (and not stale)."""
        doc = self.collection.document(self._doc_id(symbol, job_type)).get()
        if not doc.exists:
            return False
        data = doc.to_dict()
        if not data.get("locked", False):
            return False
        return not self._is_stale(data, datetime.utcnow())

    def get_retry_count(self, symbol: str, job_type: str) -> int:
        doc = self.retry_collection.document(self._doc_id(symbol, job_type)).get()
        return (doc.to_dict() or {}).get("count", 0) if doc.exists else 0

    def _log_orphan(self, lock_data: dict) -> None:
        """Audit-log an orphaned job whose stale lock was taken over."""
        try:
            self.db.collection('orphan_jobs').add({
                "job_id": lock_data.get("job_id"),
                "symbol": lock_data.get("symbol"),
                "job_type": lock_data.get("job_type"),
                "locked_at": lock_data.get("locked_at"),
                "last_heartbeat": lock_data.get("heartbeat"),
                "detected_at": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.error(f"Failed to log orphan job: {e}")


# Singleton
_lock_manager: Optional[JobLockManager] = None


def get_lock_manager() -> JobLockManager:
    """Get singleton lock manager."""
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = JobLockManager()
    return _lock_manager
