"""
Kill Switch Module

Dedicated kill switch logic for emergency trading halt.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


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
    Dedicated kill switch controller.
    
    Provides multiple trigger conditions and maintains
    activation history for analysis.
    """
    
    def __init__(
        self,
        max_consecutive_losses: int = 5,
        daily_loss_limit_pct: float = 0.05,  # 5% daily loss
        model_degradation_threshold: float = 0.30  # 30% worse than expected
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.model_degradation_threshold = model_degradation_threshold
        
        self.is_active = False
        self.activation_history: List[KillEvent] = []
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.starting_equity_today = 0.0
    
    def check_and_activate(
        self,
        current_equity: float,
        last_trade_pnl: Optional[float] = None,
        model_performance_ratio: Optional[float] = None,
        api_error: bool = False,
        data_error: bool = False
    ) -> bool:
        """
        Check all kill conditions and activate if needed.
        
        Returns True if kill switch was activated.
        """
        if self.is_active:
            return False  # Already active
        
        # Check API error
        if api_error:
            self._activate(
                KillReason.API_ERROR,
                "Critical API error detected",
                current_equity,
                0
            )
            return True
        
        # Check data error
        if data_error:
            self._activate(
                KillReason.DATA_ISSUE,
                "Data integrity issue detected",
                current_equity,
                0
            )
            return True
        
        # Check consecutive losses
        if last_trade_pnl is not None:
            if last_trade_pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
            
            if self.consecutive_losses >= self.max_consecutive_losses:
                self._activate(
                    KillReason.CONSECUTIVE_LOSSES,
                    f"{self.consecutive_losses} consecutive losing trades",
                    current_equity,
                    0
                )
                return True
        
        # Check daily loss limit
        if self.starting_equity_today > 0:
            daily_return = (current_equity - self.starting_equity_today) / self.starting_equity_today
            if daily_return < -self.daily_loss_limit_pct:
                self._activate(
                    KillReason.DAILY_LOSS_LIMIT,
                    f"Daily loss {daily_return:.2%} exceeds limit",
                    current_equity,
                    0
                )
                return True
        
        # Check model degradation
        if model_performance_ratio is not None:
            if model_performance_ratio < (1 - self.model_degradation_threshold):
                self._activate(
                    KillReason.MODEL_DEGRADATION,
                    f"Model performing at {model_performance_ratio:.0%} of expected",
                    current_equity,
                    0
                )
                return True
        
        return False
    
    def manual_activate(self, reason: str, current_equity: float) -> None:
        """Manually activate the kill switch."""
        self._activate(
            KillReason.MANUAL,
            reason,
            current_equity,
            0
        )
    
    def _activate(
        self,
        reason: KillReason,
        details: str,
        equity: float,
        positions_closed: int
    ) -> None:
        """Internal activation method."""
        self.is_active = True
        
        event = KillEvent(
            timestamp=datetime.utcnow(),
            reason=reason,
            details=details,
            equity_at_kill=equity,
            positions_closed=positions_closed
        )
        self.activation_history.append(event)
        
        logger.critical(f"KILL SWITCH ACTIVATED: {reason.value} - {details}")
    
    def reset(self, admin_key: str, expected_key: str) -> bool:
        """
        Reset the kill switch.
        
        Requires admin key verification for safety.
        """
        if admin_key != expected_key:
            logger.warning("Invalid admin key for kill switch reset")
            return False
        
        self.is_active = False
        self.consecutive_losses = 0
        logger.info("Kill switch reset by admin")
        return True
    
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
