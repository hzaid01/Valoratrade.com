"""
Trade Level Calculator

Calculates stop-loss and take-profit levels based on:
- ATR (volatility)
- Support/Resistance
- Risk/Reward ratio
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TradeLevels:
    """Calculated trade levels."""
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_ratio: float
    atr: float
    
    def to_dict(self) -> dict:
        return {
            "entry": round(self.entry, 6),
            "stop_loss": round(self.stop_loss, 6),
            "take_profit_1": round(self.take_profit_1, 6),
            "take_profit_2": round(self.take_profit_2, 6),
            "take_profit_3": round(self.take_profit_3, 6),
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "atr": round(self.atr, 6)
        }


class TradeLevelCalculator:
    """
    Calculate stop-loss and take-profit levels.
    
    Uses ATR-based dynamic levels that adapt to volatility.
    SL/TP are not arbitrary percentages but derived from
    actual market volatility.
    """
    
    def __init__(
        self,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp1_atr_multiplier: float = 1.5,
        tp2_atr_multiplier: float = 3.0,
        tp3_atr_multiplier: float = 5.0
    ):
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp1_atr_multiplier = tp1_atr_multiplier
        self.tp2_atr_multiplier = tp2_atr_multiplier
        self.tp3_atr_multiplier = tp3_atr_multiplier
    
    def calculate(
        self,
        df: pd.DataFrame,
        entry_price: float,
        direction: str,  # 'long' or 'short'
        support: Optional[float] = None,
        resistance: Optional[float] = None
    ) -> TradeLevels:
        """
        Calculate trade levels.
        
        Args:
            df: OHLCV DataFrame for ATR calculation
            entry_price: Entry price
            direction: 'long' or 'short'
            support: Optional support level
            resistance: Optional resistance level
            
        Returns:
            TradeLevels with SL and TPs
        """
        # Calculate ATR
        atr = self._calculate_atr(df)
        
        if direction == 'long':
            # Stop loss below entry
            sl = entry_price - (atr * self.sl_atr_multiplier)
            
            # If support is nearby and tighter, use it
            if support and support > sl and support < entry_price:
                sl = support - (atr * 0.2)  # Small buffer below support
            
            # Take profits above entry
            tp1 = entry_price + (atr * self.tp1_atr_multiplier)
            tp2 = entry_price + (atr * self.tp2_atr_multiplier)
            tp3 = entry_price + (atr * self.tp3_atr_multiplier)
            
            # If resistance is nearby, adjust TP1
            if resistance and resistance < tp1:
                tp1 = resistance - (atr * 0.1)
                
        else:  # short
            # Stop loss above entry
            sl = entry_price + (atr * self.sl_atr_multiplier)
            
            # If resistance is nearby and tighter, use it
            if resistance and resistance < sl and resistance > entry_price:
                sl = resistance + (atr * 0.2)
            
            # Take profits below entry
            tp1 = entry_price - (atr * self.tp1_atr_multiplier)
            tp2 = entry_price - (atr * self.tp2_atr_multiplier)
            tp3 = entry_price - (atr * self.tp3_atr_multiplier)
            
            # If support is nearby, adjust TP1
            if support and support > tp1:
                tp1 = support + (atr * 0.1)
        
        # Calculate risk/reward ratio (using TP1)
        risk = abs(entry_price - sl)
        reward = abs(tp1 - entry_price)
        rr_ratio = reward / risk if risk > 0 else 0
        
        return TradeLevels(
            entry=entry_price,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            risk_reward_ratio=rr_ratio,
            atr=atr
        )
    
    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr = pd.concat([
            high - low,
            abs(high - close.shift(1)),
            abs(low - close.shift(1))
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(self.atr_period).mean()
        
        return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else float(tr.iloc[-1])
    
    def calculate_support_resistance(
        self,
        df: pd.DataFrame,
        window: int = 20
    ) -> Tuple[float, float]:
        """Calculate support and resistance levels."""
        high = df['high']
        low = df['low']
        
        resistance = float(high.rolling(window).max().iloc[-1])
        support = float(low.rolling(window).min().iloc[-1])
        
        return support, resistance
