"""
Job Lock & Concurrency Control

Prevents concurrent training jobs and handles orphan cleanup.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
import uuid

from app.firebase_config import get_firestore

logger = logging.getLogger(__name__)


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
    
    def acquire(
        self,
        symbol: str,
        job_type: str,
        owner: str = "system"
    ) -> Optional[JobLock]:
        """
        Attempt to acquire a lock.
        
        Returns JobLock if acquired, None if locked by another.
        """
        doc_id = f"{symbol}_{job_type}"
        doc_ref = self.collection.document(doc_id)
        
        # Check existing lock
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            
            if data.get("locked", False):
                # Check if stale
                heartbeat = datetime.fromisoformat(data["heartbeat"])
                age = datetime.utcnow() - heartbeat
                
                if age > self.HEARTBEAT_TIMEOUT:
                    logger.warning(
                        f"Stale lock detected for {doc_id}, age={age}. "
                        f"Cleaning up orphan job {data['job_id']}"
                    )
                    self._cleanup_orphan(data)
                else:
                    logger.info(f"Lock {doc_id} held by {data['job_id']}")
                    return None
        
        # Check retry count
        if not self._can_retry(symbol, job_type):
            logger.error(f"Max retries exceeded for {doc_id}")
            return None
        
        # Create new lock
        job_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow()
        
        lock = JobLock(
            job_id=job_id,
            symbol=symbol,
            job_type=job_type,
            locked_at=now,
            heartbeat=now,
            owner=owner
        )
        
        doc_ref.set(lock.to_dict())
        logger.info(f"Acquired lock {doc_id} with job_id={job_id}")
        
        return lock
    
    def release(self, lock: JobLock, success: bool = True) -> None:
        """Release a lock after job completion."""
        doc_id = f"{lock.symbol}_{lock.job_type}"
        
        self.collection.document(doc_id).set({
            "locked": False,
            "last_job_id": lock.job_id,
            "last_completed": datetime.utcnow().isoformat(),
            "last_success": success
        })
        
        # Reset retry count on success
        if success:
            self._reset_retries(lock.symbol, lock.job_type)
        else:
            self._increment_retries(lock.symbol, lock.job_type)
        
        logger.info(f"Released lock {doc_id}, success={success}")
    
    def heartbeat(self, lock: JobLock) -> None:
        """Update heartbeat to indicate job is still alive."""
        doc_id = f"{lock.symbol}_{lock.job_type}"
        
        self.collection.document(doc_id).update({
            "heartbeat": datetime.utcnow().isoformat()
        })
    
    def is_locked(self, symbol: str, job_type: str) -> bool:
        """Check if a lock is currently held."""
        doc_id = f"{symbol}_{job_type}"
        doc = self.collection.document(doc_id).get()
        
        if not doc.exists:
            return False
        
        data = doc.to_dict()
        if not data.get("locked", False):
            return False
        
        # Check staleness
        heartbeat = datetime.fromisoformat(data["heartbeat"])
        age = datetime.utcnow() - heartbeat
        
        return age <= self.HEARTBEAT_TIMEOUT
    
    def _cleanup_orphan(self, lock_data: dict) -> None:
        """Clean up an orphaned job."""
        # Log orphan for audit
        self.db.collection('orphan_jobs').add({
            "job_id": lock_data["job_id"],
            "symbol": lock_data["symbol"],
            "job_type": lock_data["job_type"],
            "locked_at": lock_data["locked_at"],
            "last_heartbeat": lock_data["heartbeat"],
            "detected_at": datetime.utcnow().isoformat()
        })
        
        # Release lock
        doc_id = f"{lock_data['symbol']}_{lock_data['job_type']}"
        self.collection.document(doc_id).set({
            "locked": False,
            "orphan_cleaned": True,
            "cleaned_at": datetime.utcnow().isoformat()
        })
    
    def _can_retry(self, symbol: str, job_type: str) -> bool:
        """Check if job can be retried."""
        doc_id = f"{symbol}_{job_type}"
        doc = self.retry_collection.document(doc_id).get()
        
        if not doc.exists:
            return True
        
        data = doc.to_dict()
        return data.get("count", 0) < self.MAX_RETRIES
    
    def _increment_retries(self, symbol: str, job_type: str) -> None:
        """Increment retry count."""
        doc_id = f"{symbol}_{job_type}"
        doc = self.retry_collection.document(doc_id).get()
        
        count = 1
        if doc.exists:
            count = doc.to_dict().get("count", 0) + 1
        
        self.retry_collection.document(doc_id).set({
            "count": count,
            "last_failure": datetime.utcnow().isoformat()
        })
        
        if count >= self.MAX_RETRIES:
            logger.error(
                f"Job {doc_id} has failed {count} times. "
                f"Manual intervention required."
            )
    
    def _reset_retries(self, symbol: str, job_type: str) -> None:
        """Reset retry count after success."""
        doc_id = f"{symbol}_{job_type}"
        self.retry_collection.document(doc_id).set({
            "count": 0,
            "last_success": datetime.utcnow().isoformat()
        })


# Singleton
_lock_manager: Optional[JobLockManager] = None


def get_lock_manager() -> JobLockManager:
    """Get singleton lock manager."""
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = JobLockManager()
    return _lock_manager
