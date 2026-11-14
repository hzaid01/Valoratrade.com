"""
Global System State Machine

Enforces system-wide state transitions and service permissions.
No component should operate without checking system state.
"""
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Optional, Set
from dataclasses import dataclass, field

from app.firebase_config import get_firestore
from app.core.fs_tx import run_transaction

logger = logging.getLogger(__name__)


class SystemState(Enum):
    """
    Per-symbol pipeline/serving state.

    Live states:
      NO_DATA -> COLLECTING_DATA -> READY_FOR_TRAINING -> TRAINING
      TRAINING ends in one of:
        PROMOTED          new champion, or an existing champion keeps serving
        LIVE_DEGRADED     existing degraded champion keeps serving
        FORWARD_TESTING   no champion; challenger pending forward metrics
        REJECTED          no champion; challenger failed promotion gates
        TRAINING_FAILED   training crashed / produced invalid metrics
        COLLECTING_DATA   not enough candles (no champion)

    Deprecated (kept only so existing Firestore docs still load and can exit;
    no code path enters them any more):
      CHALLENGER_READY  replaced by FORWARD_TESTING / REJECTED / PROMOTED
      KILLED            the capital controller kill switch is the single
                        kill authority; a per-symbol KILLED duplicated it
    """
    NO_DATA = "no_data"
    COLLECTING_DATA = "collecting_data"
    READY_FOR_TRAINING = "ready_for_training"
    TRAINING = "training"
    TRAINING_FAILED = "training_failed"
    CHALLENGER_READY = "challenger_ready"  # deprecated: exits only
    FORWARD_TESTING = "forward_testing"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    LIVE_DEGRADED = "live_degraded"
    KILLED = "killed"  # deprecated: exits only


DEPRECATED_STATES: Set[SystemState] = {SystemState.CHALLENGER_READY, SystemState.KILLED}

# States in which a champion model may be serving signals.
SERVING_STATES: Set[SystemState] = {SystemState.PROMOTED, SystemState.LIVE_DEGRADED}

# Valid state transitions (the ONLY transitions transition() will perform).
VALID_TRANSITIONS: Dict[SystemState, Set[SystemState]] = {
    SystemState.NO_DATA: {SystemState.COLLECTING_DATA},
    SystemState.COLLECTING_DATA: {SystemState.READY_FOR_TRAINING, SystemState.NO_DATA},
    SystemState.READY_FOR_TRAINING: {SystemState.TRAINING},
    SystemState.TRAINING: {
        SystemState.PROMOTED,
        SystemState.LIVE_DEGRADED,
        SystemState.FORWARD_TESTING,
        SystemState.REJECTED,
        SystemState.TRAINING_FAILED,
        SystemState.COLLECTING_DATA,
    },
    SystemState.TRAINING_FAILED: {SystemState.TRAINING, SystemState.READY_FOR_TRAINING},
    SystemState.FORWARD_TESTING: {SystemState.PROMOTED, SystemState.REJECTED, SystemState.TRAINING},
    SystemState.REJECTED: {SystemState.TRAINING, SystemState.READY_FOR_TRAINING},
    SystemState.PROMOTED: {SystemState.TRAINING, SystemState.LIVE_DEGRADED},
    SystemState.LIVE_DEGRADED: {SystemState.TRAINING, SystemState.PROMOTED},
    # Deprecated: exits only
    SystemState.CHALLENGER_READY: {
        SystemState.TRAINING, SystemState.FORWARD_TESTING,
        SystemState.PROMOTED, SystemState.REJECTED,
    },
    SystemState.KILLED: {SystemState.READY_FOR_TRAINING},
}

# Service permissions by state.
# Invariant (tested): every state that permits "training" has TRAINING as a
# valid transition, so a permitted scheduled run can always start.
SERVICE_PERMISSIONS: Dict[str, Set[SystemState]] = {
    "data_ingestion": set(SystemState) - {SystemState.KILLED},
    "training": {
        SystemState.READY_FOR_TRAINING,
        SystemState.TRAINING_FAILED,   # retries are capped by the job lock
        SystemState.FORWARD_TESTING,
        SystemState.REJECTED,
        SystemState.PROMOTED,          # scheduled retrain, no force needed
        SystemState.LIVE_DEGRADED,
        SystemState.CHALLENGER_READY,  # unstick legacy docs
    },
    # A champion may be serving in any state once data exists; signal
    # availability is decided by the registry (champion present) and the
    # capital kill switch. Blocking here would silence a live champion
    # during an ordinary retrain.
    "signal_generation": set(SystemState) - {
        SystemState.NO_DATA, SystemState.COLLECTING_DATA, SystemState.KILLED,
    },
    "champion_queries": {
        SystemState.PROMOTED, SystemState.LIVE_DEGRADED, SystemState.FORWARD_TESTING,
        SystemState.TRAINING, SystemState.TRAINING_FAILED, SystemState.REJECTED,
    },
}


def resume_state_after_training(previous: Optional["StateSnapshot"]) -> SystemState:
    """
    Serving state to return to when a champion exists and training did not
    replace it. LIVE_DEGRADED is sticky across retries; anything else -> PROMOTED.
    """
    if previous is None:
        return SystemState.PROMOTED
    if previous.state == SystemState.LIVE_DEGRADED:
        return SystemState.LIVE_DEGRADED
    carried = (previous.metadata or {}).get("resume_state")
    if carried == SystemState.LIVE_DEGRADED.value:
        return SystemState.LIVE_DEGRADED
    return SystemState.PROMOTED


@dataclass
class StateSnapshot:
    """Current system state with metadata."""
    state: SystemState
    symbol: str
    updated_at: datetime
    updated_by: str
    reason: str
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "state": self.state.value,
            "symbol": self.symbol,
            "updated_at": self.updated_at.isoformat(),
            "updated_by": self.updated_by,
            "reason": self.reason,
            "metadata": self.metadata
        }


class SystemStateManager:
    """
    Manages global system state per symbol.

    Enforces:
    - Valid state transitions (read-check-write in ONE Firestore transaction,
      so two Cloud Run instances cannot both perform the same transition)
    - Service permissions
    - State persistence (Firestore ``system_state/{symbol}`` is the only
      source of truth; there is no per-process cache)
    """

    def __init__(self):
        self.db = get_firestore()
        self.collection = self.db.collection('system_state')

    # ── reads ──────────────────────────────────────────────────────────

    @staticmethod
    def _initial(symbol: str) -> StateSnapshot:
        return StateSnapshot(
            state=SystemState.NO_DATA,
            symbol=symbol,
            updated_at=datetime.utcnow(),
            updated_by="system",
            reason="Initial state"
        )

    @staticmethod
    def _from_dict(symbol: str, data: Dict) -> StateSnapshot:
        return StateSnapshot(
            state=SystemState(data["state"]),
            symbol=data.get("symbol", symbol),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            updated_by=data.get("updated_by", "unknown"),
            reason=data.get("reason", ""),
            metadata=data.get("metadata", {}) or {}
        )

    def get_state(self, symbol: str) -> StateSnapshot:
        """Get current state for a symbol (always read from Firestore)."""
        ref = self.collection.document(symbol)

        def _tx(transaction):
            snap = ref.get(transaction=transaction)
            if snap.exists:
                return self._from_dict(symbol, snap.to_dict())
            # Initialise inside the transaction so a concurrent first
            # transition on another instance cannot be overwritten.
            initial = self._initial(symbol)
            transaction.set(ref, initial.to_dict())
            return initial

        doc = ref.get()
        if doc.exists:
            return self._from_dict(symbol, doc.to_dict())
        return run_transaction(self.db, _tx)

    # ── transitions ────────────────────────────────────────────────────

    def transition(
        self,
        symbol: str,
        new_state: SystemState,
        actor: str,
        reason: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Attempt state transition atomically.

        Returns True if the transition was committed, False if it is not
        allowed from the state stored in Firestore at commit time (logged at
        ERROR). Callers must check the result.
        """
        ref = self.collection.document(symbol)

        def _tx(transaction):
            snap = ref.get(transaction=transaction)
            current = (
                self._from_dict(symbol, snap.to_dict()) if snap.exists
                else self._initial(symbol)
            )
            if new_state not in VALID_TRANSITIONS.get(current.state, set()):
                return False, current, None
            new_snapshot = StateSnapshot(
                state=new_state,
                symbol=symbol,
                updated_at=datetime.utcnow(),
                updated_by=actor,
                reason=reason,
                metadata=metadata or {}
            )
            transaction.set(ref, new_snapshot.to_dict())
            transaction.set(
                self.db.collection('state_transitions').document(),
                self._log_entry(current, new_snapshot)
            )
            return True, current, new_snapshot

        ok, current, _ = run_transaction(self.db, _tx)
        if not ok:
            logger.error(
                f"Invalid transition: {current.state.value} -> {new_state.value} "
                f"for {symbol} by {actor}. "
                f"Valid: {sorted(s.value for s in VALID_TRANSITIONS.get(current.state, set()))}"
            )
            return False
        logger.info(f"State transition: {symbol} {current.state.value} -> {new_state.value} by {actor}")
        return True

    def can_run_service(self, symbol: str, service: str) -> bool:
        """Check if a service is allowed to run in current state."""
        current = self.get_state(symbol)
        allowed_states = SERVICE_PERMISSIONS.get(service, set())

        if current.state not in allowed_states:
            logger.warning(
                f"Service '{service}' not allowed in state {current.state.value} "
                f"for {symbol}. Allowed states: {sorted(s.value for s in allowed_states)}"
            )
            return False

        return True

    def override(
        self,
        symbol: str,
        new_state: SystemState,
        actor: str,
        reason: str,
        metadata: Optional[Dict] = None
    ) -> StateSnapshot:
        """
        Bypass the transition table (operator/force paths only).

        Always logged at WARNING and written to the audit log with
        forced=True, in the same transaction as the state write.
        """
        ref = self.collection.document(symbol)
        meta = dict(metadata or {})
        meta["forced"] = True

        def _tx(transaction):
            snap = ref.get(transaction=transaction)
            current = (
                self._from_dict(symbol, snap.to_dict()) if snap.exists
                else self._initial(symbol)
            )
            new_snapshot = StateSnapshot(
                state=new_state,
                symbol=symbol,
                updated_at=datetime.utcnow(),
                updated_by=actor,
                reason=f"FORCED: {reason}",
                metadata=meta
            )
            transaction.set(ref, new_snapshot.to_dict())
            entry = self._log_entry(current, new_snapshot)
            entry["forced"] = True
            transaction.set(self.db.collection('state_transitions').document(), entry)
            return current, new_snapshot

        current, new_snapshot = run_transaction(self.db, _tx)
        logger.warning(
            f"FORCED state change: {symbol} {current.state.value} -> {new_state.value} "
            f"by {actor}: {reason}"
        )
        return new_snapshot

    def force_state(
        self,
        symbol: str,
        new_state: SystemState,
        admin_key: str,
        reason: str
    ) -> bool:
        """
        Force state change (admin only, bypasses transition rules).

        The key is checked against the ADMIN_SECRET_KEY env var (previously a
        hardcoded literal). Use with caution - for emergency recovery only.
        """
        import os
        expected_key = os.getenv("ADMIN_SECRET_KEY")
        if not expected_key or admin_key != expected_key:
            logger.error(f"Invalid admin key for force_state on {symbol}")
            return False
        self.override(symbol, new_state, actor="admin_force", reason=reason)
        return True

    @staticmethod
    def _log_entry(old: StateSnapshot, new: StateSnapshot) -> Dict:
        return {
            "symbol": new.symbol,
            "from_state": old.state.value,
            "to_state": new.state.value,
            "actor": new.updated_by,
            "reason": new.reason,
            "timestamp": new.updated_at.isoformat()
        }


# Singleton instance
_state_manager: Optional[SystemStateManager] = None


def get_state_manager() -> SystemStateManager:
    """Get singleton state manager."""
    global _state_manager
    if _state_manager is None:
        _state_manager = SystemStateManager()
    return _state_manager


def require_state(allowed_states: Set[SystemState]):
    """
    Decorator to enforce state requirements on functions.
    
    Usage:
        @require_state({SystemState.READY_FOR_TRAINING})
        async def train_model(symbol: str):
            ...
    """
    def decorator(func):
        async def wrapper(symbol: str, *args, **kwargs):
            manager = get_state_manager()
            current = manager.get_state(symbol)
            
            if current.state not in allowed_states:
                raise StateViolationError(
                    f"Function {func.__name__} requires state in "
                    f"{[s.value for s in allowed_states]}, "
                    f"but {symbol} is in {current.state.value}"
                )
            
            return await func(symbol, *args, **kwargs)
        return wrapper
    return decorator


class StateViolationError(Exception):
    """Raised when an operation violates state constraints."""
    pass
