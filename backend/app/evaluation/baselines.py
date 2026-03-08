"""
Baseline Strategies

Naive benchmark strategies that ANY AI model MUST beat:
- Buy & Hold
- EMA Crossover
- Breakout
- Random Entry

No model enters production without beating ALL baselines.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np

from app.evaluation.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


class BaselineStrategy(Enum):
    """Available baseline strategies."""
    BUY_AND_HOLD = "buy_and_hold"
    EMA_CROSSOVER = "ema_crossover"
    BREAKOUT = "breakout"
    RANDOM_ENTRY = "random_entry"


@dataclass
class BaselineResult:
    """Result from running a baseline strategy."""
    strategy: BaselineStrategy
    metrics: Dict
    trades: List[Dict]
    equity_curve: pd.Series
    computed_at: datetime
    
    def to_dict(self) -> Dict:
        return {
            "strategy": self.strategy.value,
            "metrics": self.metrics,
            "trade_count": len(self.trades),
            "final_equity": float(self.equity_curve.iloc[-1]),
            "computed_at": self.computed_at.isoformat()
        }


class BaselineStrategies:
    """
    Implementation of naive baseline strategies.
    
    These are the minimum performance benchmarks.
    If AI cannot beat a simple EMA crossover, it has no value.
    """
    
    def __init__(
        self,
        initial_capital: float = 100000.0,
        fee_rate: float = 0.001,  # 0.1% per trade
        slippage: float = 0.0005  # 0.05% slippage
    ):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.metrics_calculator = PerformanceMetrics()
    
    def run_all(self, df: pd.DataFrame) -> Dict[str, BaselineResult]:
        """Run all baseline strategies and return results."""
        results = {}
        
        for strategy in BaselineStrategy:
            result = self.run_strategy(df, strategy)
            results[strategy.value] = result
            logger.info(f"Baseline {strategy.value}: Sharpe={result.metrics.get('sharpe_ratio', 0):.2f}")
        
        return results
    
    def run_strategy(
        self,
        df: pd.DataFrame,
        strategy: BaselineStrategy
    ) -> BaselineResult:
        """Run a specific baseline strategy."""
        df = df.copy()
        
        if strategy == BaselineStrategy.BUY_AND_HOLD:
            equity_curve, trades = self._buy_and_hold(df)
        elif strategy == BaselineStrategy.EMA_CROSSOVER:
            equity_curve, trades = self._ema_crossover(df)
        elif strategy == BaselineStrategy.BREAKOUT:
            equity_curve, trades = self._breakout(df)
        elif strategy == BaselineStrategy.RANDOM_ENTRY:
            equity_curve, trades = self._random_entry(df)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
        
        # Calculate metrics
        metrics = self.metrics_calculator.calculate_all(equity_curve, trades)
        
        return BaselineResult(
            strategy=strategy,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            computed_at=datetime.utcnow()
        )
    
    def _buy_and_hold(self, df: pd.DataFrame) -> Tuple[pd.Series, List[Dict]]:
        """Buy at start, hold until end."""
        close = df['close']
        
        # Single trade: buy at first candle
        entry_price = close.iloc[0] * (1 + self.slippage)
        entry_fee = self.initial_capital * self.fee_rate
        
        # Calculate equity curve
        position_size = (self.initial_capital - entry_fee) / entry_price
        equity = position_size * close
        
        # Exit at end
        exit_price = close.iloc[-1] * (1 - self.slippage)
        exit_fee = position_size * exit_price * self.fee_rate
        final_equity = (position_size * exit_price) - exit_fee
        
        equity_curve = pd.Series(equity.values, index=df.index)
        
        trades = [{
            "entry_time": df.index[0],
            "exit_time": df.index[-1],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "direction": "long",
            "pnl": final_equity - self.initial_capital,
            "return_pct": (final_equity / self.initial_capital) - 1
        }]
        
        return equity_curve, trades
    
    def _ema_crossover(
        self,
        df: pd.DataFrame,
        fast_period: int = 9,
        slow_period: int = 21
    ) -> Tuple[pd.Series, List[Dict]]:
        """
        EMA crossover strategy.
        
        Long when fast EMA crosses above slow EMA.
        Exit when fast EMA crosses below slow EMA.
        """
        close = df['close']
        
        ema_fast = close.ewm(span=fast_period).mean()
        ema_slow = close.ewm(span=slow_period).mean()
        
        # Generate signals
        signals = pd.Series(0, index=df.index)
        signals[ema_fast > ema_slow] = 1   # Long signal
        signals[ema_fast < ema_slow] = -1  # Exit/short signal
        
        return self._simulate_signals(df, signals)
    
    def _breakout(
        self,
        df: pd.DataFrame,
        lookback: int = 20
    ) -> Tuple[pd.Series, List[Dict]]:
        """
        Breakout strategy.
        
        Long when price breaks above N-period high.
        Exit when price breaks below N-period low.
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        upper = high.rolling(lookback).max().shift(1)
        lower = low.rolling(lookback).min().shift(1)
        
        signals = pd.Series(0, index=df.index)
        signals[close > upper] = 1   # Breakout long
        signals[close < lower] = -1  # Breakdown exit
        
        return self._simulate_signals(df, signals)
    
    def _random_entry(
        self,
        df: pd.DataFrame,
        trade_probability: float = 0.05,  # 5% chance per candle
        seed: int = 42
    ) -> Tuple[pd.Series, List[Dict]]:
        """
        Random entry strategy (luck baseline).
        
        Random long/short entries with fixed holding period.
        """
        np.random.seed(seed)
        
        n = len(df)
        signals = pd.Series(0, index=df.index)
        
        # Random entries
        for i in range(n):
            if np.random.random() < trade_probability:
                direction = np.random.choice([1, -1])
                signals.iloc[i] = direction
        
        return self._simulate_signals(df, signals, random_exits=True)
    
    def _simulate_signals(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        random_exits: bool = False,
        hold_periods: int = 12
    ) -> Tuple[pd.Series, List[Dict]]:
        """
        Simulate trading based on signals.
        
        Args:
            df: OHLCV data
            signals: Signal series (1=long, -1=short/exit, 0=hold)
            random_exits: Use fixed holding period for exits
            hold_periods: Periods to hold if using random exits
            
        Returns:
            equity_curve, trades list
        """
        # close = df['close'] - unused
        equity = self.initial_capital
        position = 0  # 0=flat, 1=long
        position_size = 0.0
        entry_price = 0.0
        entry_idx = 0
        
        equity_curve = []
        trades = []
        
        for i, (idx, row) in enumerate(df.iterrows()):
            price = row['close']
            signal = signals.iloc[i]
            
            # Check for exit
            should_exit = False
            if position == 1:
                if random_exits:
                    should_exit = (i - entry_idx) >= hold_periods
                else:
                    should_exit = signal <= 0
            
            if should_exit and position == 1:
                # Exit long
                exit_price = price * (1 - self.slippage)
                exit_fee = position_size * exit_price * self.fee_rate
                pnl = (exit_price - entry_price) * position_size - exit_fee
                equity += pnl
                
                trades.append({
                    "entry_time": df.index[entry_idx],
                    "exit_time": idx,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "direction": "long",
                    "pnl": pnl,
                    "return_pct": pnl / (entry_price * position_size)
                })
                
                position = 0
                position_size = 0.0
            
            # Check for entry
            if position == 0 and signal == 1:
                entry_price = price * (1 + self.slippage)
                entry_fee = equity * self.fee_rate
                position_size = (equity - entry_fee) / entry_price
                position = 1
                entry_idx = i
                equity -= entry_fee
            
            # Update equity curve
            if position == 1:
                current_equity = equity + (price - entry_price) * position_size
            else:
                current_equity = equity
            
            equity_curve.append(current_equity)
        
        return pd.Series(equity_curve, index=df.index), trades
    
    def get_baseline_requirements(self) -> Dict:
        """Get the minimum requirements a model must beat."""
        return {
            "description": "Model must beat ALL baseline strategies",
            "strategies": [s.value for s in BaselineStrategy],
            "metrics_to_beat": [
                "sharpe_ratio",
                "total_return",
                "max_drawdown"
            ],
            "rules": [
                "Model Sharpe must exceed best baseline Sharpe by 10%",
                "Model return must exceed buy_and_hold return",
                "Model max drawdown must not exceed 1.5x worst baseline drawdown"
            ]
        }
    
    def compare_to_model(
        self,
        model_metrics: Dict,
        baseline_results: Dict[str, BaselineResult]
    ) -> Dict:
        """
        Compare model to baselines and determine if it passes.
        
        Returns comparison report with pass/fail status.
        """
        best_baseline_sharpe = max(
            r.metrics.get('sharpe_ratio', 0) for r in baseline_results.values()
        )
        bnh_return = baseline_results['buy_and_hold'].metrics.get('total_return', 0)
        worst_baseline_dd = max(
            r.metrics.get('max_drawdown', 0) for r in baseline_results.values()
        )
        
        model_sharpe = model_metrics.get('sharpe_ratio', 0)
        model_return = model_metrics.get('total_return', 0)
        model_dd = model_metrics.get('max_drawdown', 0)
        
        checks = {
            "beats_baseline_sharpe": model_sharpe > best_baseline_sharpe * 1.1,
            "beats_buy_and_hold": model_return > bnh_return,
            "acceptable_drawdown": model_dd < worst_baseline_dd * 1.5
        }
        
        passed = all(checks.values())
        
        return {
            "passed": passed,
            "checks": checks,
            "model_metrics": {
                "sharpe": model_sharpe,
                "return": model_return,
                "drawdown": model_dd
            },
            "baseline_benchmarks": {
                "best_sharpe": best_baseline_sharpe,
                "buy_hold_return": bnh_return,
                "worst_drawdown": worst_baseline_dd
            }
        }
