"""
Target Engineering

Implements trading-specific target generation:
- Triple Barrier Method
- Volatility-adjusted labels
- Multi-horizon targets
- Regime-conditioned targets

Targets reflect trading opportunity, NOT just price movement.
"""
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Optional
import pandas as pd
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)


class BarrierLabel(IntEnum):
    """Triple barrier outcome labels."""
    STOP_LOSS = -1
    TIMEOUT = 0
    TAKE_PROFIT = 1


@dataclass
class TargetResult:
    """Result of target labeling for a single point."""
    label: int
    horizon: int  # Candles until barrier hit
    return_pct: float
    barrier_type: str  # 'tp', 'sl', 'timeout'
    volatility_at_entry: float


@dataclass
class TargetSet:
    """Container for all targets for a dataset."""
    labels: pd.DataFrame
    version: str
    config: Dict
    
    def get_classification_targets(self) -> np.ndarray:
        """Get labels for classification (-1, 0, 1)."""
        return self.labels['barrier_label'].values
    
    def get_regression_targets(self) -> np.ndarray:
        """Get return values for regression."""
        return self.labels['forward_return'].values
    
    def get_multi_horizon_targets(self) -> Dict[str, np.ndarray]:
        """Get targets for each horizon."""
        result = {}
        for col in self.labels.columns:
            if col.startswith('prob_up_') or col.startswith('prob_down_'):
                result[col] = self.labels[col].values
        return result


class TripleBarrierLabeler:
    """
    Triple Barrier Method for label generation.
    
    Creates labels based on which barrier is hit first:
    - Upper barrier (take profit)
    - Lower barrier (stop loss)
    - Vertical barrier (max holding period)
    
    Barriers are volatility-adjusted using ATR.
    """
    
    VERSION = "1.0.0"
    
    def __init__(
        self,
        profit_target_atr: float = 2.0,  # TP at 2x ATR
        stop_loss_atr: float = 1.0,      # SL at 1x ATR
        max_holding_periods: int = 24,    # 24 hours max
        min_return_threshold: float = 0.005  # 0.5% min for non-zero label
    ):
        self.profit_target_atr = profit_target_atr
        self.stop_loss_atr = stop_loss_atr
        self.max_holding = max_holding_periods
        self.min_return = min_return_threshold
    
    def label_dataset(
        self,
        df: pd.DataFrame,
        atr_period: int = 14
    ) -> TargetSet:
        """
        Label entire dataset with triple barrier method.
        
        Args:
            df: OHLCV DataFrame
            atr_period: Period for ATR calculation
            
        Returns:
            TargetSet with all labels
        """
        df = df.copy()
        
        # Compute ATR for volatility-adjusted barriers
        df['atr'] = self._compute_atr(df, atr_period)
        
        # Initialize label columns
        df['barrier_label'] = 0
        df['barrier_horizon'] = 0
        df['forward_return'] = 0.0
        df['barrier_type'] = 'none'
        
        # Label each point (excluding last max_holding candles)
        for i in range(len(df) - self.max_holding - 1):
            result = self._label_point(df, i)
            if result:
                df.iloc[i, df.columns.get_loc('barrier_label')] = result.label
                df.iloc[i, df.columns.get_loc('barrier_horizon')] = result.horizon
                df.iloc[i, df.columns.get_loc('forward_return')] = result.return_pct
                df.iloc[i, df.columns.get_loc('barrier_type')] = result.barrier_type
        
        # Add multi-horizon probability targets
        df = self._add_multi_horizon_targets(df)
        
        config = {
            "profit_target_atr": self.profit_target_atr,
            "stop_loss_atr": self.stop_loss_atr,
            "max_holding_periods": self.max_holding,
            "atr_period": atr_period
        }
        
        return TargetSet(
            labels=df[[
                'barrier_label', 'barrier_horizon', 'forward_return',
                'barrier_type', 'prob_up_4', 'prob_up_8', 'prob_up_12', 'prob_up_24',
                'prob_down_4', 'prob_down_8', 'prob_down_12', 'prob_down_24'
            ]].copy(),
            version=self.VERSION,
            config=config
        )
    
    def _label_point(self, df: pd.DataFrame, idx: int) -> Optional[TargetResult]:
        """Label a single point using triple barrier."""
        entry_price = df.iloc[idx]['close']
        atr = df.iloc[idx]['atr']
        
        if pd.isna(atr) or atr == 0:
            return None
        
        # Volatility-adjusted barriers
        upper_barrier = entry_price + (atr * self.profit_target_atr)
        lower_barrier = entry_price - (atr * self.stop_loss_atr)
        
        # Check future candles
        for h in range(1, self.max_holding + 1):
            if idx + h >= len(df):
                break
            
            future_high = df.iloc[idx + h]['high']
            future_low = df.iloc[idx + h]['low']
            # future_close unused
            
            # Check upper barrier (take profit)
            if future_high >= upper_barrier:
                return_pct = (upper_barrier - entry_price) / entry_price
                return TargetResult(
                    label=BarrierLabel.TAKE_PROFIT,
                    horizon=h,
                    return_pct=return_pct,
                    barrier_type='tp',
                    volatility_at_entry=atr / entry_price
                )
            
            # Check lower barrier (stop loss)
            if future_low <= lower_barrier:
                return_pct = (lower_barrier - entry_price) / entry_price
                return TargetResult(
                    label=BarrierLabel.STOP_LOSS,
                    horizon=h,
                    return_pct=return_pct,
                    barrier_type='sl',
                    volatility_at_entry=atr / entry_price
                )
        
        # Timeout - use final return
        final_idx = min(idx + self.max_holding, len(df) - 1)
        final_price = df.iloc[final_idx]['close']
        return_pct = (final_price - entry_price) / entry_price
        
        # Assign label based on return magnitude
        if abs(return_pct) < self.min_return:
            label = BarrierLabel.TIMEOUT
        elif return_pct > 0:
            label = BarrierLabel.TAKE_PROFIT
        else:
            label = BarrierLabel.STOP_LOSS
        
        return TargetResult(
            label=label,
            horizon=self.max_holding,
            return_pct=return_pct,
            barrier_type='timeout',
            volatility_at_entry=atr / entry_price
        )
    
    def _add_multi_horizon_targets(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add probability targets for multiple horizons."""
        close = df['close']
        
        for horizon in [4, 8, 12, 24]:
            # Forward returns
            fwd_return = close.shift(-horizon) / close - 1
            
            # Binary up/down with threshold
            df[f'prob_up_{horizon}'] = (fwd_return > self.min_return).astype(int)
            df[f'prob_down_{horizon}'] = (fwd_return < -self.min_return).astype(int)
        
        return df
    
    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr = pd.concat([
            high - low,
            abs(high - close.shift(1)),
            abs(low - close.shift(1))
        ], axis=1).max(axis=1)
        
        return tr.rolling(period).mean()


class TargetEngineer:
    """
    High-level target engineering coordinator.
    
    Combines multiple target types and handles:
    - Class imbalance analysis
    - Target profitability reporting
    - Regime-conditioned targets
    """
    
    def __init__(self):
        settings = get_settings()
        self.labeler = TripleBarrierLabeler(
            profit_target_atr=settings.target.profit_target / 0.01,  # Convert % to ATR multiple
            stop_loss_atr=settings.target.stop_loss / 0.01,
            max_holding_periods=settings.target.max_holding_periods
        )
    
    def generate_targets(
        self,
        df: pd.DataFrame,
        regime_labels: Optional[pd.Series] = None
    ) -> TargetSet:
        """
        Generate all targets for a dataset.
        
        Args:
            df: OHLCV DataFrame
            regime_labels: Optional regime labels for conditioning
            
        Returns:
            TargetSet with comprehensive targets
        """
        target_set = self.labeler.label_dataset(df)
        
        # Add regime conditioning if provided
        if regime_labels is not None:
            target_set = self._add_regime_conditioning(target_set, regime_labels)
        
        return target_set
    
    def analyze_class_balance(self, target_set: TargetSet) -> Dict:
        """Analyze class distribution in targets."""
        labels = target_set.get_classification_targets()
        
        total = len(labels)
        counts = {
            'stop_loss': int((labels == -1).sum()),
            'timeout': int((labels == 0).sum()),
            'take_profit': int((labels == 1).sum())
        }
        
        ratios = {k: v / total for k, v in counts.items()}
        
        return {
            'counts': counts,
            'ratios': ratios,
            'is_balanced': all(0.2 < r < 0.5 for r in ratios.values()),
            'majority_class': max(counts, key=counts.get)
        }
    
    def get_profitability_report(self, target_set: TargetSet) -> Dict:
        """Generate target profitability analysis."""
        labels = target_set.labels
        
        # Win rate by horizon
        horizon_stats = {}
        for h in [4, 8, 12, 24]:
            up_col = f'prob_up_{h}'
            down_col = f'prob_down_{h}'
            
            if up_col in labels.columns:
                horizon_stats[f'{h}h'] = {
                    'up_rate': float(labels[up_col].mean()),
                    'down_rate': float(labels[down_col].mean()),
                    'neutral_rate': float(1 - labels[up_col].mean() - labels[down_col].mean())
                }
        
        # Average return by label
        avg_returns = {}
        for label in [-1, 0, 1]:
            mask = labels['barrier_label'] == label
            avg_returns[label] = float(labels.loc[mask, 'forward_return'].mean())
        
        return {
            'horizon_stats': horizon_stats,
            'avg_return_by_label': avg_returns,
            'overall_avg_return': float(labels['forward_return'].mean()),
            'avg_holding_period': float(labels['barrier_horizon'].mean())
        }
    
    def _add_regime_conditioning(
        self,
        target_set: TargetSet,
        regime_labels: pd.Series
    ) -> TargetSet:
        """Adjust targets based on regime."""
        # For now, just add regime as a column
        # Future: could weight targets differently by regime
        target_set.labels['regime'] = regime_labels.values[:len(target_set.labels)]
        return target_set
