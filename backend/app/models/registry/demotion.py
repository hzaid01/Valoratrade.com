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
    
    def __init__(self):
        self.db = get_firestore()
        self.registry = ModelRegistry()
        self.state_manager = get_state_manager()
        self._demotion_history: List[DemotionEvent] = []
    
    def check_degradation(
        self,
        symbol: str,
        forward_metrics: Dict
    ) -> Optional[DemotionEvent]:
        """
        Check if champion has degraded and should be demoted.
        
        Returns DemotionEvent if demotion triggered, None otherwise.
        """
        champion = self.registry.get_champion(symbol)
        if not champion:
            return None
        
        # Get benchmark (validation accuracy at training time)
        benchmark_accuracy = champion.validation_metrics.get("accuracy", 0)
        
        # Get current rolling accuracy from forward metrics
        current_accuracy = forward_metrics.get("rolling_accuracy", 0)
        
        # Check accuracy degradation
        if benchmark_accuracy > 0:
            degradation = (benchmark_accuracy - current_accuracy) / benchmark_accuracy
            
            if degradation > self.ACCURACY_DEGRADATION_THRESHOLD:
                return self._trigger_demotion(
                    champion.model_id,
                    symbol,
                    reason=f"Accuracy degraded {degradation:.1%} below benchmark",
                    metrics=forward_metrics
                )
        
        # Check drawdown
        current_drawdown = forward_metrics.get("max_drawdown", 0)
        if current_drawdown > self.MAX_DRAWDOWN:
            return self._trigger_demotion(
                champion.model_id,
                symbol,
                reason=f"Drawdown {current_drawdown:.1%} exceeds limit {self.MAX_DRAWDOWN:.1%}",
                metrics=forward_metrics
            )
        
        # Check consecutive failures
        consecutive_failures = forward_metrics.get("consecutive_failures", 0)
        if consecutive_failures >= self.CONSECUTIVE_FAILURE_LIMIT:
            return self._trigger_demotion(
                champion.model_id,
                symbol,
                reason=f"{consecutive_failures} consecutive prediction failures",
                metrics=forward_metrics
            )
        
        return None
    
    def _trigger_demotion(
        self,
        model_id: str,
        symbol: str,
        reason: str,
        metrics: Dict
    ) -> DemotionEvent:
        """Execute demotion and rollback."""
        logger.warning(f"Demoting model {model_id}: {reason}")
        
        # Find rollback target
        rollback_target = self._find_rollback_target(symbol)
        
        # Retire current champion
        self.registry.retire(model_id)
        
        # Promote rollback target or go to baseline-only mode
        if rollback_target:
            self.registry.promote_to_champion(rollback_target)
            logger.info(f"Rolled back to {rollback_target}")
        else:
            # No good rollback target - transition to degraded state
            self.state_manager.transition(
                symbol=symbol,
                new_state=SystemState.LIVE_DEGRADED,
                actor="demotion_system",
                reason=f"No rollback target available. Original demotion: {reason}"
            )
            logger.warning(f"No rollback target for {symbol}, entering LIVE_DEGRADED state")
        
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
    
    def _find_rollback_target(self, symbol: str) -> Optional[str]:
        """Find best model to rollback to."""
        models = self.registry.list_models(symbol=symbol)
        
        # Filter to non-champion, non-retired models
        candidates = [
            m for m in models
            if m.status == "registered"
            and m.validation_metrics.get("sharpe_ratio", 0) > 0
        ]
        
        if not candidates:
            return None
        
        # Pick best by sharpe ratio
        best = max(candidates, key=lambda m: m.validation_metrics.get("sharpe_ratio", 0))
        return best.model_id
    
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
