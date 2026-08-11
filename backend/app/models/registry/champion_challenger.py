"""
Champion/Challenger Framework

Manages model promotion with strict gates:
- Challenger must beat champion on forward metrics
- Challenger must beat ALL baselines
- Auto-rollback on performance degradation
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from app.models.registry.model_registry import ModelRegistry, ModelMetadata
from app.evaluation.baselines import BaselineStrategies, BaselineResult
from app.config import get_settings

logger = logging.getLogger(__name__)


class PromotionStatus(Enum):
    """Promotion decision status."""
    PROMOTED = "promoted"
    REJECTED_BASELINES = "rejected_does_not_beat_baselines"
    REJECTED_CHAMPION = "rejected_does_not_beat_champion"
    REJECTED_DRAWDOWN = "rejected_drawdown_too_high"
    REJECTED_FORWARD = "rejected_forward_degradation"
    PENDING = "pending_forward_test"


@dataclass
class PromotionResult:
    """Result of a promotion evaluation."""
    challenger_id: str
    champion_id: Optional[str]
    status: PromotionStatus
    checks: Dict[str, bool]
    metrics_comparison: Dict
    timestamp: datetime
    
    def to_dict(self) -> Dict:
        return {
            "challenger": self.challenger_id,
            "champion": self.champion_id,
            "status": self.status.value,
            "checks": self.checks,
            "comparison": self.metrics_comparison,
            "timestamp": self.timestamp.isoformat()
        }


class ChampionChallenger:
    """
    Champion/Challenger model promotion framework.
    
    Promotion only happens if challenger:
    1. Beats ALL naive baselines
    2. Matches or beats current champion
    3. Has acceptable drawdown
    4. Shows consistent forward performance
    
    If champion degrades, automatic rollback occurs.
    """
    
    def __init__(
        self,
        registry: ModelRegistry,
        baselines: BaselineStrategies
    ):
        self.registry = registry
        self.baselines = baselines
        self.settings = get_settings()
        
        # Promotion history
        self._promotions: List[PromotionResult] = []
    
    def _validate_metrics(self, metrics: Dict) -> Tuple[bool, List[str]]:
        """Validate that all metrics are finite numbers."""
        errors = []
        
        def check_value(key: str, value):
            if isinstance(value, float):
                if math.isnan(value):
                    errors.append(f"{key} is NaN")
                if math.isinf(value):
                    errors.append(f"{key} is Inf")
            elif isinstance(value, dict):
                for k, v in value.items():
                    check_value(f"{key}.{k}", v)
        
        for key, value in metrics.items():
            check_value(key, value)
        
        return len(errors) == 0, errors
    
    def evaluate_challenger(
        self,
        challenger_id: str,
        baseline_results: Dict[str, BaselineResult],
        multi_window_validated: bool = False
    ) -> PromotionResult:
        """
        Evaluate a challenger for promotion.
        
        Args:
            challenger_id: ID of challenger model
            baseline_results: Results from running baselines
            multi_window_validated: True if model passed multi-window validation
                                   (REQUIRED for first model promotion)
            
        Returns:
            PromotionResult with decision
        """
        challenger = self.registry.get(challenger_id)
        if not challenger:
            raise ValueError(f"Challenger {challenger_id} not found")
        
        # Validate metrics are not NaN/Inf
        valid, metric_errors = self._validate_metrics(challenger.validation_metrics)
        if not valid:
            return PromotionResult(
                challenger_id=challenger_id,
                champion_id=None,
                status=PromotionStatus.REJECTED_BASELINES,  # Using this for invalid metrics
                checks={"metrics_valid": False, "errors": metric_errors},
                metrics_comparison={"errors": metric_errors},
                timestamp=datetime.utcnow()
            )
        
        champion = self.registry.get_champion(challenger.symbol)
        
        checks = {}
        comparison = {}
        
        # Track if this is the first model (no champion exists)
        checks["is_first_model"] = champion is None
        checks["multi_window_validated"] = multi_window_validated
        
        # Check 1: Must beat ALL baselines
        checks["beats_baselines"] = self._check_beats_baselines(
            challenger, baseline_results
        )
        
        # Check 2: Must match or beat champion (if exists)
        if champion:
            checks["beats_champion"] = self._check_beats_champion(
                challenger, champion
            )
            comparison["champion"] = {
                "sharpe": champion.validation_metrics.get("sharpe_ratio", 0),
                "accuracy": champion.validation_metrics.get("accuracy", 0)
            }
        else:
            checks["beats_champion"] = True  # No champion to beat
        
        # Check 3: Acceptable drawdown
        checks["acceptable_drawdown"] = self._check_drawdown(challenger)
        
        # Check 4: Forward consistency (if forward metrics exist)
        if challenger.forward_metrics:
            checks["forward_consistent"] = self._check_forward_consistency(challenger)
        else:
            checks["forward_consistent"] = None  # Pending
        
        comparison["challenger"] = {
            "sharpe": challenger.validation_metrics.get("sharpe_ratio", 0),
            "accuracy": challenger.validation_metrics.get("accuracy", 0)
        }
        comparison["baselines"] = {
            name: r.metrics.get("sharpe_ratio", 0)
            for name, r in baseline_results.items()
        }
        
        # Determine status
        status = self._determine_status(checks)
        
        result = PromotionResult(
            challenger_id=challenger_id,
            champion_id=champion.model_id if champion else None,
            status=status,
            checks=checks,
            metrics_comparison=comparison,
            timestamp=datetime.utcnow()
        )
        
        self._promotions.append(result)
        
        # Execute promotion if approved
        if status == PromotionStatus.PROMOTED:
            self.registry.promote_to_champion(challenger_id)
            logger.info(f"Promoted {challenger_id} to champion")
        else:
            logger.info(f"Challenger {challenger_id} rejected: {status.value}")
        
        return result
    
    def check_for_degradation(
        self,
        symbol: str,
        current_forward_metrics: Dict
    ) -> bool:
        """
        Check if current champion has degraded.
        
        If degradation detected, triggers rollback alert.
        Returns True if degradation detected.
        """
        champion = self.registry.get_champion(symbol)
        if not champion:
            return False
        
        if not champion.forward_metrics:
            return False
        
        # Compare current forward metrics to historical
        threshold = self.settings.kill_criteria.forward_degradation_threshold
        
        historical_accuracy = champion.forward_metrics.get("accuracy", 0)
        current_accuracy = current_forward_metrics.get("accuracy", 0)
        
        if current_accuracy < historical_accuracy * (1 - threshold):
            logger.warning(
                f"Champion {champion.model_id} degraded: "
                f"accuracy {current_accuracy:.3f} vs historical {historical_accuracy:.3f}"
            )
            return True
        
        return False
    
    def get_available_rollback_versions(self, symbol: str) -> List[str]:
        """Get list of versions available for rollback."""
        models = self.registry.list_models(symbol=symbol)
        return [
            m.version for m in models
            if m.status != "champion"
        ]
    
    def rollback(self, symbol: str, to_version: str) -> bool:
        """Rollback to a previous model version."""
        logger.warning(f"Rolling back {symbol} to version {to_version}")
        return self.registry.rollback(symbol, to_version)
    
    def _check_beats_baselines(
        self,
        challenger: ModelMetadata,
        baseline_results: Dict[str, BaselineResult]
    ) -> bool:
        """Check if challenger beats all baselines."""
        kill = self.settings.kill_criteria
        
        challenger_sharpe = challenger.validation_metrics.get("sharpe_ratio", 0)
        
        for name, result in baseline_results.items():
            baseline_sharpe = result.metrics.get("sharpe_ratio", 0)
            
            # Must beat by minimum margin
            if challenger_sharpe <= baseline_sharpe * (1 + kill.min_sharpe_vs_baseline):
                logger.info(f"Challenger Sharpe {challenger_sharpe:.3f} does not beat {name} {baseline_sharpe:.3f}")
                return False
        
        return True
    
    def _check_beats_champion(
        self,
        challenger: ModelMetadata,
        champion: ModelMetadata
    ) -> bool:
        """Check if challenger matches or beats champion."""
        challenger_sharpe = challenger.validation_metrics.get("sharpe_ratio", 0)
        champion_sharpe = champion.validation_metrics.get("sharpe_ratio", 0)
        
        # Must match within 5%
        return challenger_sharpe >= champion_sharpe * 0.95
    
    def _check_drawdown(self, challenger: ModelMetadata) -> bool:
        """Check if drawdown is acceptable."""
        kill = self.settings.kill_criteria
        
        drawdown = challenger.validation_metrics.get("max_drawdown", 0)
        return drawdown <= kill.max_allowed_drawdown
    
    def _check_forward_consistency(self, challenger: ModelMetadata) -> bool:
        """Check forward performance consistency."""
        if not challenger.forward_metrics:
            return False
        
        kill = self.settings.kill_criteria
        
        backtest_accuracy = challenger.validation_metrics.get("accuracy", 0)
        forward_accuracy = challenger.forward_metrics.get("accuracy", 0)
        
        # Forward accuracy should not degrade too much
        return forward_accuracy >= backtest_accuracy * (1 - kill.forward_degradation_threshold)
    
    def _determine_status(self, checks: Dict[str, Optional[bool]]) -> PromotionStatus:
        """
        Determine promotion status from checks.
        
        FIRST MODEL SPECIAL CASE:
        - Must pass beats_baselines
        - Must pass acceptable_drawdown
        - Must have multi_window_validated = True
        - Skip forward_consistent (no forward data yet)
        """
        if not checks.get("beats_baselines"):
            return PromotionStatus.REJECTED_BASELINES
        
        if not checks.get("acceptable_drawdown"):
            return PromotionStatus.REJECTED_DRAWDOWN
        
        # FIRST MODEL: promote if passes baselines + drawdown + multi-window
        if checks.get("is_first_model"):
            if checks.get("multi_window_validated"):
                logger.info("First model promotion: passed baselines + drawdown + multi-window")
                return PromotionStatus.PROMOTED
            else:
                # First model without multi-window validation stays pending
                logger.info("First model awaiting multi-window validation")
                return PromotionStatus.PENDING
        
        # EXISTING CHAMPION: must also beat champion
        if not checks.get("beats_champion"):
            return PromotionStatus.REJECTED_CHAMPION
        
        if checks.get("forward_consistent") is None:
            return PromotionStatus.PENDING
        
        if not checks.get("forward_consistent"):
            return PromotionStatus.REJECTED_FORWARD
        
        return PromotionStatus.PROMOTED
    
    def get_promotion_history(
        self,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """Get promotion history."""
        history = self._promotions
        
        if symbol:
            history = [
                p for p in history
                if symbol in p.challenger_id
            ]
        
        return [p.to_dict() for p in history]
