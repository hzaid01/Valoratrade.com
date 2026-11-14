"""
Kill Switch Module

Trading-mode state machine plus the automatic trigger evaluator.

Single source of truth: ``KillSwitchState`` stored in Firestore
``system_state/capital_controller.trading_mode``. Nothing else (no
is_killed / is_active / is_forced_flat flags) decides whether trading is
halted. ``CapitalController`` owns reads/writes of that field; this module
defines the states, events and transition table, and evaluates automatic
kill conditions without mutating state itself.

States and transitions (anything not listed raises IllegalKillSwitchTransition):

    | from         | event    | to                                   |
    |--------------|----------|--------------------------------------|
    | ACTIVE       | KILL     | KILLED                               |
    | ACTIVE       | FLAT_ON  | FORCED_FLAT                          |
    | ACTIVE       | FLAT_OFF | ACTIVE   (idempotent no-op)          |
    | ACTIVE       | RESET    | ACTIVE   (idempotent no-op)          |
    | FORCED_FLAT  | KILL     | KILLED   (resume_mode=FORCED_FLAT)   |
    | FORCED_FLAT  | FLAT_ON  | FORCED_FLAT (idempotent no-op)       |
    | FORCED_FLAT  | FLAT_OFF | ACTIVE                               |
    | FORCED_FLAT  | RESET    | FORCED_FLAT (idempotent no-op)       |
    | KILLED       | KILL     | KILLED   (idempotent no-op)          |
    | KILLED       | RESET    | resume_mode (ACTIVE or FORCED_FLAT)  |
    | KILLED       | FLAT_ON  | illegal                              |
    | KILLED       | FLAT_OFF | illegal                              |
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class KillSwitchState(Enum):
    """The single trading-mode state."""
    ACTIVE = "active"            # normal trading (drawdown throttling is a sizing rule, not a mode)
    FORCED_FLAT = "forced_flat"  # no new trades, positions closed
    KILLED = "killed"            # no trading at all; admin reset required


class KillSwitchEvent(Enum):
    KILL = "kill"
    RESET = "reset"
    FLAT_ON = "flat_on"
    FLAT_OFF = "flat_off"


class IllegalKillSwitchTransition(Exception):
    """Raised for a (state, event) pair not in the transition table."""


# "RESUME" is resolved at apply time from the stored resume_mode.
_RESUME = "resume"

KILLSWITCH_TRANSITIONS: Dict[Tuple[KillSwitchState, KillSwitchEvent], object] = {
    (KillSwitchState.ACTIVE, KillSwitchEvent.KILL): KillSwitchState.KILLED,
    (KillSwitchState.ACTIVE, KillSwitchEvent.FLAT_ON): KillSwitchState.FORCED_FLAT,
    (KillSwitchState.ACTIVE, KillSwitchEvent.FLAT_OFF): KillSwitchState.ACTIVE,
    (KillSwitchState.ACTIVE, KillSwitchEvent.RESET): KillSwitchState.ACTIVE,
    (KillSwitchState.FORCED_FLAT, KillSwitchEvent.KILL): KillSwitchState.KILLED,
    (KillSwitchState.FORCED_FLAT, KillSwitchEvent.FLAT_ON): KillSwitchState.FORCED_FLAT,
    (KillSwitchState.FORCED_FLAT, KillSwitchEvent.FLAT_OFF): KillSwitchState.ACTIVE,
    (KillSwitchState.FORCED_FLAT, KillSwitchEvent.RESET): KillSwitchState.FORCED_FLAT,
    (KillSwitchState.KILLED, KillSwitchEvent.KILL): KillSwitchState.KILLED,
    (KillSwitchState.KILLED, KillSwitchEvent.RESET): _RESUME,
}


def next_trading_mode(
    state: KillSwitchState,
    event: KillSwitchEvent,
    resume_mode: Optional[KillSwitchState] = None
) -> KillSwitchState:
    """Pure transition function. Raises IllegalKillSwitchTransition."""
    target = KILLSWITCH_TRANSITIONS.get((state, event))
    if target is None:
        raise IllegalKillSwitchTransition(f"{event.value} not allowed in state {state.value}")
    if target == _RESUME:
        return resume_mode if resume_mode in (KillSwitchState.ACTIVE, KillSwitchState.FORCED_FLAT) \
            else KillSwitchState.ACTIVE
    return target


def killswitch_auto_mode() -> str:
    """
    KILLSWITCH_AUTO=log_only|enforce (default log_only).

    Governs AUTOMATIC activation only (drawdown, consecutive losses, daily
    loss, API/data errors, model degradation). Manual admin activation is
    always enforced. Unknown values fall back to log_only.
    """
    raw = os.getenv("KILLSWITCH_AUTO", "log_only").strip().lower()
    if raw not in ("log_only", "enforce"):
        logger.warning(f"Unknown KILLSWITCH_AUTO={raw!r}; using log_only")
        return "log_only"
    return raw


class KillReason(Enum):
    """Reasons for kill switch activation."""
    MAX_DRAWDOWN = "max_drawdown_exceeded"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    API_ERROR = "critical_api_error"
    MODEL_DEGRADATION = "model_performance_degradation"
    MANUAL = "manual_activation"
    DATA_ISSUE = "data_integrity_issue"
    SYSTEM_ERROR = "system_error"


@dataclass
class KillEvent:
    """Record of a kill switch activation."""
    timestamp: datetime
    reason: KillReason
    details: str
    equity_at_kill: float
    positions_closed: int


class KillSwitch:
    """
    Automatic kill-condition evaluator and activation history.

    Holds counters (consecutive losses, daily starting equity) but NOT the
    trading mode. ``is_active`` is a read-only view of the owner's mode.
    """

    def __init__(
        self,
        max_consecutive_losses: int = 5,
        daily_loss_limit_pct: float = 0.05,  # 5% daily loss
        model_degradation_threshold: float = 0.30,  # 30% worse than expected
        mode_provider: Optional[Callable[[], KillSwitchState]] = None
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.model_degradation_threshold = model_degradation_threshold
        self._mode_provider = mode_provider

        self.activation_history: List[KillEvent] = []
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.starting_equity_today = 0.0

    @property
    def is_active(self) -> bool:
        if self._mode_provider is None:
            return False
        return self._mode_provider() == KillSwitchState.KILLED

    def evaluate(
        self,
        current_equity: float,
        last_trade_pnl: Optional[float] = None,
        model_performance_ratio: Optional[float] = None,
        api_error: bool = False,
        data_error: bool = False
    ) -> Optional[Tuple[KillReason, str]]:
        """
        Update counters and return (reason, details) if a kill condition is
        met, else None. Does not change any state.
        """
        if last_trade_pnl is not None:
            self.daily_pnl += last_trade_pnl
            if last_trade_pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

        if api_error:
            return KillReason.API_ERROR, "Critical API error detected"
        if data_error:
            return KillReason.DATA_ISSUE, "Data integrity issue detected"
        if self.consecutive_losses >= self.max_consecutive_losses:
            return KillReason.CONSECUTIVE_LOSSES, f"{self.consecutive_losses} consecutive losing trades"
        if self.starting_equity_today > 0:
            daily_return = (current_equity - self.starting_equity_today) / self.starting_equity_today
            if daily_return < -self.daily_loss_limit_pct:
                return KillReason.DAILY_LOSS_LIMIT, f"Daily loss {daily_return:.2%} exceeds limit"
        if model_performance_ratio is not None:
            if model_performance_ratio < (1 - self.model_degradation_threshold):
                return (
                    KillReason.MODEL_DEGRADATION,
                    f"Model performing at {model_performance_ratio:.0%} of expected"
                )
        return None

    def record_activation(self, reason: KillReason, details: str, equity: float, positions_closed: int = 0) -> None:
        self.activation_history.append(KillEvent(
            timestamp=datetime.utcnow(),
            reason=reason,
            details=details,
            equity_at_kill=equity,
            positions_closed=positions_closed
        ))
        logger.critical(f"KILL SWITCH ACTIVATED: {reason.value} - {details}")

    def on_reset(self) -> None:
        self.consecutive_losses = 0

    def start_new_day(self, current_equity: float) -> None:
        """Reset daily tracking."""
        self.starting_equity_today = current_equity
        self.daily_pnl = 0.0

    def get_status(self) -> dict:
        """Get current kill switch status."""
        return {
            "is_active": self.is_active,
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl": self.daily_pnl,
            "activation_count": len(self.activation_history),
            "last_activation": (
                self.activation_history[-1].timestamp.isoformat()
                if self.activation_history else None
            )
        }

    def get_history(self) -> List[dict]:
        """Get activation history."""
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "reason": e.reason.value,
                "details": e.details,
                "equity_at_kill": e.equity_at_kill
            }
            for e in self.activation_history
        ]
