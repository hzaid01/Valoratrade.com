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

logger = logging.getLogger(__name__)


class SystemState(Enum):
    """Global system states."""
    NO_DATA = "no_data"
    COLLECTING_DATA = "collecting_data"
    READY_FOR_TRAINING = "ready_for_training"
    TRAINING = "training"
    TRAINING_FAILED = "training_failed"
    CHALLENGER_READY = "challenger_ready"
    FORWARD_TESTING = "forward_testing"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    LIVE_DEGRADED = "live_degraded"
    KILLED = "killed"


# Valid state transitions
VALID_TRANSITIONS: Dict[SystemState, Set[SystemState]] = {
    SystemState.NO_DATA: {SystemState.COLLECTING_DATA},
    SystemState.COLLECTING_DATA: {SystemState.READY_FOR_TRAINING, SystemState.NO_DATA},
    SystemState.READY_FOR_TRAINING: {SystemState.TRAINING, SystemState.KILLED},
    SystemState.TRAINING: {SystemState.TRAINING_FAILED, SystemState.CHALLENGER_READY},
    SystemState.TRAINING_FAILED: {SystemState.READY_FOR_TRAINING, SystemState.KILLED},
    SystemState.CHALLENGER_READY: {SystemState.FORWARD_TESTING, SystemState.REJECTED},
    SystemState.FORWARD_TESTING: {SystemState.PROMOTED, SystemState.REJECTED},
    SystemState.PROMOTED: {SystemState.LIVE_DEGRADED, SystemState.KILLED, SystemState.TRAINING},
    SystemState.REJECTED: {SystemState.READY_FOR_TRAINING},
    SystemState.LIVE_DEGRADED: {SystemState.KILLED, SystemState.PROMOTED, SystemState.TRAINING},
    SystemState.KILLED: {SystemState.READY_FOR_TRAINING},  # Manual reset only
}

# Service permissions by state
SERVICE_PERMISSIONS: Dict[str, Set[SystemState]] = {
    "data_ingestion": {
        SystemState.NO_DATA, SystemState.COLLECTING_DATA, 
        SystemState.READY_FOR_TRAINING, SystemState.TRAINING,
        SystemState.TRAINING_FAILED, SystemState.CHALLENGER_READY,
        SystemState.FORWARD_TESTING, SystemState.PROMOTED,
        SystemState.REJECTED, SystemState.LIVE_DEGRADED
        # NOT KILLED
    },
    "training": {
        SystemState.READY_FOR_TRAINING
    },
    "signal_generation": {
        SystemState.PROMOTED, SystemState.LIVE_DEGRADED
    },
    "champion_queries": {
        SystemState.PROMOTED, SystemState.LIVE_DEGRADED, SystemState.FORWARD_TESTING
    },
}


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
    - Valid state transitions
    - Service permissions
    - State persistence
    """
    
    def __init__(self):
        self.db = get_firestore()
        self.collection = self.db.collection('system_state')
        self._cache: Dict[str, StateSnapshot] = {}
    
    def get_state(self, symbol: str) -> StateSnapshot:
        """Get current state for a symbol."""
        # Check cache first
        if symbol in self._cache:
            cache_age = datetime.utcnow() - self._cache[symbol].updated_at
            if cache_age < timedelta(seconds=30):
                return self._cache[symbol]
        
        # Fetch from Firestore
        doc = self.collection.document(symbol).get()
        
        if not doc.exists:
            # Initialize new symbol
            initial = StateSnapshot(
                state=SystemState.NO_DATA,
                symbol=symbol,
                updated_at=datetime.utcnow(),
                updated_by="system",
                reason="Initial state"
            )
            self._save_state(initial)
            return initial
        
        data = doc.to_dict()
        snapshot = StateSnapshot(
            state=SystemState(data["state"]),
            symbol=data["symbol"],
            updated_at=datetime.fromisoformat(data["updated_at"]),
            updated_by=data["updated_by"],
            reason=data["reason"],
            metadata=data.get("metadata", {})
        )
        
        self._cache[symbol] = snapshot
        return snapshot
    
    def transition(
        self,
        symbol: str,
        new_state: SystemState,
        actor: str,
        reason: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Attempt state transition.
        
        Returns True if transition succeeded, False if invalid.
        """
        current = self.get_state(symbol)
        
        # Check if transition is valid
        if new_state not in VALID_TRANSITIONS.get(current.state, set()):
            logger.error(
                f"Invalid transition: {current.state.value} -> {new_state.value} "
                f"for {symbol}. Valid: {VALID_TRANSITIONS.get(current.state, set())}"
            )
            return False
        
        # Create new state
        new_snapshot = StateSnapshot(
            state=new_state,
            symbol=symbol,
            updated_at=datetime.utcnow(),
            updated_by=actor,
            reason=reason,
            metadata=metadata or {}
        )
        
        self._save_state(new_snapshot)
        
        # Log transition
        self._log_transition(current, new_snapshot)
        
        logger.info(f"State transition: {symbol} {current.state.value} -> {new_state.value}")
        return True
    
    def can_run_service(self, symbol: str, service: str) -> bool:
        """Check if a service is allowed to run in current state."""
        current = self.get_state(symbol)
        allowed_states = SERVICE_PERMISSIONS.get(service, set())
        
        if current.state not in allowed_states:
            logger.warning(
                f"Service '{service}' not allowed in state {current.state.value} "
                f"for {symbol}. Allowed states: {[s.value for s in allowed_states]}"
            )
            return False
        
        return True
    
    def force_state(
        self,
        symbol: str,
        new_state: SystemState,
        admin_key: str,
        reason: str
    ) -> bool:
        """
        Force state change (admin only, bypasses transition rules).
        
        Use with caution - for emergency recovery only.
        """
        expected_key = "ADMIN_FORCE_KEY"  # In production, from env
        
        if admin_key != expected_key:
            logger.error(f"Invalid admin key for force_state on {symbol}")
            return False
        
        new_snapshot = StateSnapshot(
            state=new_state,
            symbol=symbol,
            updated_at=datetime.utcnow(),
            updated_by="admin_force",
            reason=f"FORCED: {reason}",
            metadata={"forced": True}
        )
        
        self._save_state(new_snapshot)
        logger.warning(f"FORCED state change: {symbol} -> {new_state.value}")
        return True
    
    def _save_state(self, snapshot: StateSnapshot) -> None:
        """Save state to Firestore."""
        self.collection.document(snapshot.symbol).set(snapshot.to_dict())
        self._cache[snapshot.symbol] = snapshot
    
    def _log_transition(self, old: StateSnapshot, new: StateSnapshot) -> None:
        """Log state transition for audit."""
        log_entry = {
            "symbol": new.symbol,
            "from_state": old.state.value,
            "to_state": new.state.value,
            "actor": new.updated_by,
            "reason": new.reason,
            "timestamp": new.updated_at.isoformat()
        }
        
        # Append to transition log
        self.db.collection('state_transitions').add(log_entry)


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
