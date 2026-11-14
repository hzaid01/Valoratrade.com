"""
Auto-Demotion System

Monitors champion performance and triggers demotion/rollback.
"""
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.firebase_config import get_firestore
from app.models.registry.model_registry import ModelRegistry
from app.core.system_state import get_state_manager, SystemState

logger = logging.getLogger(__name__)


@dataclass
class DemotionEvent:
    """Record of a demotion event."""
    model_id: str
    symbol: str
    reason: str
    demoted_at: datetime
    rolled_back_to: Optional[str]
    metrics_at_demotion: Dict


class ModelDemotion:
    """
    Monitors champion models and triggers demotion on degradation.
    
    Demotion triggers:
    - Rolling window accuracy drops > 20% below benchmark
    - Forward drawdown exceeds 15%
    - 3+ consecutive prediction failures
    - Manual admin trigger
    """
    
    ROLLING_WINDOW_SIZE = 50  # predictions
    ACCURACY_DEGRADATION_THRESHOLD = 0.20  # 20% drop
    MAX_DRAWDOWN = 0.15
    CONSECUTIVE_FAILURE_LIMIT = 3
    
    def __init__(self, registry: Optional[ModelRegistry] = None, state_manager=None):
        self.db = get_firestore()
        self.registry = registry or ModelRegistry()
        self.state_manager = state_manager or get_state_manager()
        self._demotion_history: List[DemotionEvent] = []

    def evaluate(self, symbol: str, forward_metrics: Dict) -> Optional[str]:
        """
        Pure check: the demotion reason if the champion should be demoted,
        else None. Never changes anything. Missing metrics never count as
        degradation (the old code read a missing "rolling_accuracy" as 0,
        i.e. 100% degraded).
        """
        champion = self.registry.get_champion(symbol)
        if not champion:
            return None

        benchmark_accuracy = champion.validation_metrics.get("accuracy", 0) or 0
        current_accuracy = forward_metrics.get("rolling_accuracy")
        if benchmark_accuracy > 0 and current_accuracy is not None:
            degradation = (benchmark_accuracy - current_accuracy) / benchmark_accuracy
            if degradation > self.ACCURACY_DEGRADATION_THRESHOLD:
                return f"Accuracy degraded {degradation:.1%} below benchmark"

        current_drawdown = forward_metrics.get("max_drawdown")
        if current_drawdown is not None and current_drawdown > self.MAX_DRAWDOWN:
            return f"Drawdown {current_drawdown:.1%} exceeds limit {self.MAX_DRAWDOWN:.1%}"

        consecutive_failures = forward_metrics.get("consecutive_failures")
        if consecutive_failures is not None and consecutive_failures >= self.CONSECUTIVE_FAILURE_LIMIT:
            return f"{consecutive_failures} consecutive prediction failures"
        return None
    
    def check_degradation(
        self,
        symbol: str,
        forward_metrics: Dict
    ) -> Optional[DemotionEvent]:
        """
        Check if champion has degraded and should be demoted.
        
        Returns DemotionEvent if demotion triggered, None otherwise.
        """
        reason = self.evaluate(symbol, forward_metrics)
        if reason is None:
            return None
        champion = self.registry.get_champion(symbol)
        return self._trigger_demotion(champion.model_id, symbol, reason=reason, metrics=forward_metrics)
    
    def _trigger_demotion(
        self,
        model_id: str,
        symbol: str,
        reason: str,
        metrics: Dict
    ) -> DemotionEvent:
        """Execute demotion and rollback."""
        logger.warning(f"Demoting model {model_id}: {reason}")
        
        # Find rollback target (former good champion only)
        rollback_target = self._find_rollback_target(symbol, exclude=model_id)
        
        # Retire current champion as DEMOTED so it is never a rollback target
        self.registry.retire(model_id, reason="demoted")
        
        # Promote rollback target or go to degraded mode. Re-promoting a
        # retired model is an override by design: audited, actor=demotion.
        if rollback_target and self.registry.promote_to_champion(
            rollback_target, override=True, actor="demotion_rollback",
            reason=f"rollback after demoting {model_id}: {reason}"
        ):
            logger.info(f"Rolled back to {rollback_target}")
        else:
            # No good rollback target - transition to degraded state
            if self.state_manager.transition(
                symbol=symbol,
                new_state=SystemState.LIVE_DEGRADED,
                actor="demotion_system",
                reason=f"No rollback target available. Original demotion: {reason}"
            ):
                logger.warning(f"No rollback target for {symbol}, entering LIVE_DEGRADED state")
            else:
                logger.error(
                    f"[{symbol}] demotion could not enter LIVE_DEGRADED from "
                    f"{self.state_manager.get_state(symbol).state.value}"
                )
        
        event = DemotionEvent(
            model_id=model_id,
            symbol=symbol,
            reason=reason,
            demoted_at=datetime.utcnow(),
            rolled_back_to=rollback_target,
            metrics_at_demotion=metrics
        )
        
        self._demotion_history.append(event)
        self._log_demotion(event)
        
        return event
    
    def _find_rollback_target(self, symbol: str, exclude: Optional[str] = None) -> Optional[str]:
        """
        Best model to roll back to: only former champions retired because
        they were superseded. Previously this picked status=="registered"
        models, i.e. challengers that had FAILED promotion.
        """
        return self.registry.find_rollback_target(symbol, exclude=exclude)
    
    def _log_demotion(self, event: DemotionEvent) -> None:
        """Log demotion to Firestore for audit."""
        self.db.collection("demotion_events").add({
            "model_id": event.model_id,
            "symbol": event.symbol,
            "reason": event.reason,
            "demoted_at": event.demoted_at.isoformat(),
            "rolled_back_to": event.rolled_back_to,
            "metrics": event.metrics_at_demotion
        })
    
    def force_demotion(
        self,
        symbol: str,
        reason: str = "Admin forced demotion"
    ) -> Optional[DemotionEvent]:
        """Force demotion of current champion (admin use)."""
        champion = self.registry.get_champion(symbol)
        if not champion:
            logger.warning(f"No champion to demote for {symbol}")
            return None
        
        return self._trigger_demotion(
            champion.model_id,
            symbol,
            reason=reason,
            metrics={"forced": True}
        )
    
    def get_demotion_history(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get demotion history."""
        history = self._demotion_history
        
        if symbol:
            history = [e for e in history if e.symbol == symbol]
        
        return [
            {
                "model_id": e.model_id,
                "symbol": e.symbol,
                "reason": e.reason,
                "demoted_at": e.demoted_at.isoformat(),
                "rolled_back_to": e.rolled_back_to
            }
            for e in history
        ]
