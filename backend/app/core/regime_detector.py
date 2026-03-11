"""
Market Regime Detection

Identifies current market regime to adjust trading behavior:
- TRENDING: Strong directional movement (ADX > 25)
- RANGING: Sideways/choppy market (ADX < 20)
- HIGH_VOLATILITY: Elevated volatility requiring caution
- LOW_VOLATILITY: Compressed volatility, potential breakout
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime states."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"
    
    @property
    def is_tradeable(self) -> bool:
        """Check if regime allows trading."""
        return self in (
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
            MarketRegime.LOW_VOLATILITY,
            MarketRegime.RANGING  # RANGING now tradeable
        )
    
    @property
    def position_size_multiplier(self) -> float:
        """Get position size adjustment for regime."""
        multipliers = {
            MarketRegime.TRENDING_UP: 1.0,
            MarketRegime.TRENDING_DOWN: 1.0,
            MarketRegime.RANGING: 0.5,
            MarketRegime.HIGH_VOLATILITY: 0.3,
            MarketRegime.LOW_VOLATILITY: 0.8,
            MarketRegime.UNKNOWN: 0.0
        }
        return multipliers.get(self, 0.5)


@dataclass
class RegimeState:
    """Current regime state with supporting metrics."""
    regime: MarketRegime
    confidence: float  # 0-1
    adx: float
    volatility_ratio: float
    trend_direction: float  # -1 to 1
    timestamp: pd.Timestamp
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for API response."""
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 3),
            "adx": round(self.adx, 2),
            "volatility_ratio": round(self.volatility_ratio, 3),
            "trend_direction": round(self.trend_direction, 3),
            "is_tradeable": self.regime.is_tradeable,
            "position_multiplier": self.regime.position_size_multiplier
        }


class RegimeDetector:
    """
    Detects market regime using ADX, volatility, and trend indicators.
    """
    
    ADX_TRENDING = 25.0
    ADX_RANGING = 20.0
    VOLATILITY_HIGH_RATIO = 1.5
    VOLATILITY_LOW_RATIO = 0.7
    
    def __init__(self):
        self._history: list = []
        self._max_history = 100
    
    def detect(self, df: pd.DataFrame, features: Optional[Dict[str, float]] = None) -> RegimeState:
        if len(df) < 50:
            return RegimeState(
                regime=MarketRegime.UNKNOWN,
                confidence=0.0,
                adx=0.0,
                volatility_ratio=1.0,
                trend_direction=0.0,
                timestamp=df.index[-1] if not df.empty else pd.Timestamp.now()
            )
        
        adx = self._compute_adx(df)
        volatility_ratio = self._compute_volatility_ratio(df)
        trend_direction = self._compute_trend_direction(df)
        
        regime, confidence = self._classify_regime(adx, volatility_ratio, trend_direction)
        
        state = RegimeState(
            regime=regime,
            confidence=confidence,
            adx=adx,
            volatility_ratio=volatility_ratio,
            trend_direction=trend_direction,
            timestamp=df.index[-1]
        )
        
        self._history.append(state)
        if len(self._history) > self._max_history:
            self._history.pop(0)
        
        return state
    
    def _compute_adx(self, df: pd.DataFrame) -> float:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr = pd.concat([
            high - low,
            abs(high - close.shift(1)),
            abs(low - close.shift(1))
        ], axis=1).max(axis=1)
        
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        period = 14
        atr = tr.rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        
        return float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0
    
    def _compute_volatility_ratio(self, df: pd.DataFrame) -> float:
        returns = df['close'].pct_change()
        current_vol = returns.tail(20).std()
        historical_vol = returns.tail(100).std()
        
        if historical_vol == 0:
            return 1.0
        
        return float(current_vol / historical_vol)
    
    def _compute_trend_direction(self, df: pd.DataFrame) -> float:
        close = df['close']
        
        ema_20 = close.ewm(span=20).mean()
        ema_50 = close.ewm(span=50).mean()
        ema_score = (ema_20.iloc[-1] - ema_50.iloc[-1]) / ema_50.iloc[-1]
        
        momentum = close.pct_change(20).iloc[-1]
        direction = 0.6 * np.tanh(ema_score * 100) + 0.4 * np.tanh(momentum * 20)
        
        return float(np.clip(direction, -1, 1))
    
    def _classify_regime(self, adx: float, volatility_ratio: float, trend_direction: float) -> Tuple[MarketRegime, float]:
        if volatility_ratio > self.VOLATILITY_HIGH_RATIO:
            confidence = min((volatility_ratio - 1) / 0.5, 1.0)
            return MarketRegime.HIGH_VOLATILITY, confidence
        
        if volatility_ratio < self.VOLATILITY_LOW_RATIO:
            confidence = min((1 - volatility_ratio) / 0.3, 1.0)
            return MarketRegime.LOW_VOLATILITY, confidence
        
        if adx > self.ADX_TRENDING:
            confidence = min((adx - 20) / 20, 1.0)
            if trend_direction > 0.2:
                return MarketRegime.TRENDING_UP, confidence
            elif trend_direction < -0.2:
                return MarketRegime.TRENDING_DOWN, confidence
        
        if adx < self.ADX_RANGING:
            confidence = min((25 - adx) / 10, 1.0)
            return MarketRegime.RANGING, confidence
        
        if trend_direction > 0.1:
            return MarketRegime.TRENDING_UP, 0.5
        elif trend_direction < -0.1:
            return MarketRegime.TRENDING_DOWN, 0.5
        
        return MarketRegime.RANGING, 0.5
    
    def get_regime_history(self, periods: int = 24) -> list:
        return [s.to_dict() for s in self._history[-periods:]]
    
    def is_regime_stable(self, lookback: int = 6) -> bool:
        if len(self._history) < lookback:
            return False
        
        recent = self._history[-lookback:]
        regimes = [s.regime for s in recent]
        
        return len(set(regimes)) == 1