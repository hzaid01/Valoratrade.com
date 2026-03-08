"""
Position Sizer

Calculates optimal position size based on:
- Available capital
- Risk per trade
- Volatility
- Regime
"""
import logging
from dataclasses import dataclass

from app.core.regime_detector import MarketRegime
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PositionSize:
    """Position sizing result."""
    size: float
    size_usd: float
    risk_amount: float
    position_pct: float
    adjustments: dict


class PositionSizer:
    """
    Position sizing based on Kelly criterion and volatility.
    
    Base position size is adjusted by:
    - Confidence (higher = larger)
    - Volatility (higher = smaller)
    - Regime (choppy = smaller)
    - Drawdown (higher = smaller)
    """
    
    def __init__(
        self,
        max_position_pct: float = 0.10,  # Max 10% per trade
        base_risk_pct: float = 0.02,     # 2% risk per trade
        kelly_fraction: float = 0.25     # Quarter Kelly
    ):
        self.max_position_pct = max_position_pct
        self.base_risk_pct = base_risk_pct
        self.kelly_fraction = kelly_fraction
        
        settings = get_settings()
        self.max_exposure = settings.capital.max_exposure
    
    def calculate(
        self,
        available_capital: float,
        entry_price: float,
        stop_loss_price: float,
        confidence: float,
        volatility_score: float,
        regime: MarketRegime,
        current_drawdown_pct: float = 0.0
    ) -> PositionSize:
        """
        Calculate position size.
        
        Args:
            available_capital: Available capital for trading
            entry_price: Planned entry price
            stop_loss_price: Planned stop loss price
            confidence: Model confidence (0-1)
            volatility_score: Current volatility (0-1)
            regime: Current market regime
            current_drawdown_pct: Current drawdown (0-1)
            
        Returns:
            PositionSize with calculated size
        """
        adjustments = {}
        
        # Base position based on risk per trade
        risk_pct = entry_price - stop_loss_price
        if risk_pct <= 0:
            risk_pct = entry_price * 0.02  # Default 2% risk
        
        risk_amount = available_capital * self.base_risk_pct
        base_size = risk_amount / abs(risk_pct)
        
        # Adjustment 1: Kelly sizing based on confidence
        kelly_size = self._kelly_size(confidence, base_size)
        adjustments["kelly"] = kelly_size / base_size if base_size > 0 else 1.0
        
        # Adjustment 2: Volatility reduction
        vol_multiplier = self._volatility_adjustment(volatility_score)
        adjustments["volatility"] = vol_multiplier
        
        # Adjustment 3: Regime reduction
        regime_multiplier = regime.position_size_multiplier
        adjustments["regime"] = regime_multiplier
        
        # Adjustment 4: Drawdown reduction
        dd_multiplier = self._drawdown_adjustment(current_drawdown_pct)
        adjustments["drawdown"] = dd_multiplier
        
        # Combine adjustments
        final_size = kelly_size * vol_multiplier * regime_multiplier * dd_multiplier
        
        # Apply caps
        max_size_by_capital = (available_capital * self.max_position_pct) / entry_price
        final_size = min(final_size, max_size_by_capital)
        
        # Ensure positive
        final_size = max(final_size, 0)
        
        return PositionSize(
            size=final_size,
            size_usd=final_size * entry_price,
            risk_amount=final_size * abs(risk_pct),
            position_pct=final_size * entry_price / available_capital if available_capital > 0 else 0,
            adjustments=adjustments
        )
    
    def _kelly_size(self, confidence: float, base_size: float) -> float:
        """Apply Kelly criterion sizing."""
        # Estimated win rate from confidence
        win_rate = confidence
        
        # Assume 1:1 risk/reward ratio for simplicity
        win_loss_ratio = 1.0
        
        # Kelly formula: f = p - (1-p)/b
        # Where p = win probability, b = win/loss ratio
        kelly_pct = win_rate - (1 - win_rate) / win_loss_ratio
        
        # Apply fraction of Kelly (more conservative)
        adjusted_kelly = max(0, kelly_pct * self.kelly_fraction)
        
        return base_size * (1 + adjusted_kelly)
    
    def _volatility_adjustment(self, volatility_score: float) -> float:
        """Reduce size in high volatility."""
        if volatility_score <= 0.3:
            return 1.0
        elif volatility_score <= 0.6:
            return 0.8
        elif volatility_score <= 0.8:
            return 0.5
        else:
            return 0.3
    
    def _drawdown_adjustment(self, drawdown_pct: float) -> float:
        """Reduce size during drawdown."""
        if drawdown_pct <= 0.05:
            return 1.0
        elif drawdown_pct <= 0.10:
            return 0.7
        elif drawdown_pct <= 0.15:
            return 0.4
        else:
            return 0.2
