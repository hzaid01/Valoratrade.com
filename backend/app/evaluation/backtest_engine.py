"""
Backtesting Engine

Candle-by-candle simulation on 1H data with:
- Realistic slippage and fees
- Equity curve tracking
- No lookahead bias
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pandas as pd

from app.evaluation.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Individual trade record."""
    symbol: str
    direction: str  # 'long' or 'short'
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    size: float = 0.0
    pnl: float = 0.0
    return_pct: float = 0.0
    exit_reason: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "size": self.size,
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "exit_reason": self.exit_reason
        }


@dataclass
class BacktestResult:
    """Complete backtest result."""
    symbol: str
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float
    metrics: Dict
    trades: List[Trade]
    equity_curve: pd.Series
    drawdown_curve: pd.Series
    config: Dict
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "period": {
                "start": self.start_date.isoformat(),
                "end": self.end_date.isoformat()
            },
            "capital": {
                "initial": self.initial_capital,
                "final": self.final_equity,
                "return_pct": (self.final_equity / self.initial_capital) - 1
            },
            "metrics": self.metrics,
            "trade_count": len(self.trades),
            "config": self.config
        }


class BacktestEngine:
    """
    Production-grade backtesting engine.
    
    Features:
    - Candle-by-candle simulation (no vectorized lookahead)
    - Configurable slippage and fees
    - Support for stop-loss and take-profit
    - Capital-aware position sizing
    - Regime-aware trading
    """
    
    def __init__(
        self,
        initial_capital: float = 100000.0,
        fee_rate: float = 0.001,      # 0.1% per trade
        slippage: float = 0.0005,     # 0.05% slippage
        max_position_pct: float = 0.95  # Max 95% of capital per trade
    ):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.max_position_pct = max_position_pct
        self.metrics = PerformanceMetrics()
    
    def run(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        symbol: str = "BTCUSDT",
        strategy_name: str = "model",
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None
    ) -> BacktestResult:
        """
        Run backtest with provided signals.
        
        Args:
            df: OHLCV DataFrame (1H candles)
            signals: Signal series (1=long, -1=short, 0=flat)
            symbol: Trading symbol
            strategy_name: Name for identification
            stop_loss_pct: Optional stop loss percentage
            take_profit_pct: Optional take profit percentage
            
        Returns:
            BacktestResult with full analysis
        """
        # Validate inputs
        if len(df) != len(signals):
            raise ValueError("DataFrame and signals must have same length")
        
        # Initialize state
        equity = self.initial_capital
        position: Optional[Trade] = None
        trades: List[Trade] = []
        equity_curve = []
        
        # Candle-by-candle simulation
        for i, (idx, row) in enumerate(df.iterrows()):
            signal = signals.iloc[i]
            current_price = row['close']
            
            # Update position PnL if holding
            if position is not None:
                unrealized_pnl = self._calculate_unrealized_pnl(
                    position, current_price
                )
                current_equity = equity + unrealized_pnl
                
                # Check stop loss / take profit
                should_exit, exit_reason = self._check_exit_conditions(
                    position, row, stop_loss_pct, take_profit_pct
                )
                
                if should_exit or signal != (1 if position.direction == 'long' else -1):
                    # Close position
                    exit_price = self._get_exit_price(
                        current_price, position.direction
                    )
                    realized_pnl = self._close_position(
                        position, exit_price, idx
                    )
                    equity += realized_pnl
                    position.exit_reason = exit_reason or "signal"
                    trades.append(position)
                    position = None
            else:
                current_equity = equity
            
            # Open new position if signal
            if position is None and signal != 0:
                direction = 'long' if signal == 1 else 'short'
                position = self._open_position(
                    symbol, direction, current_price, idx, equity
                )
                equity -= position.size * position.entry_price * self.fee_rate
            
            equity_curve.append(current_equity)
        
        # Close any remaining position
        if position is not None:
            exit_price = self._get_exit_price(
                df.iloc[-1]['close'], position.direction
            )
            realized_pnl = self._close_position(
                position, exit_price, df.index[-1]
            )
            equity += realized_pnl
            position.exit_reason = "end_of_backtest"
            trades.append(position)
            equity_curve[-1] = equity
        
        # Build result
        equity_series = pd.Series(equity_curve, index=df.index)
        peak = equity_series.expanding().max()
        drawdown_series = (peak - equity_series) / peak
        
        trade_dicts = [t.to_dict() for t in trades]
        metrics = self.metrics.calculate_all(equity_series, trade_dicts)
        
        return BacktestResult(
            symbol=symbol,
            strategy_name=strategy_name,
            start_date=df.index[0],
            end_date=df.index[-1],
            initial_capital=self.initial_capital,
            final_equity=equity,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_series,
            drawdown_curve=drawdown_series,
            config={
                "fee_rate": self.fee_rate,
                "slippage": self.slippage,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct
            }
        )
    
    def _open_position(
        self,
        symbol: str,
        direction: str,
        price: float,
        timestamp: datetime,
        available_capital: float
    ) -> Trade:
        """Open a new position."""
        # Apply slippage
        if direction == 'long':
            entry_price = price * (1 + self.slippage)
        else:
            entry_price = price * (1 - self.slippage)
        
        # Calculate position size
        max_capital = available_capital * self.max_position_pct
        size = max_capital / entry_price
        
        return Trade(
            symbol=symbol,
            direction=direction,
            entry_time=timestamp,
            entry_price=entry_price,
            size=size
        )
    
    def _close_position(
        self,
        position: Trade,
        exit_price: float,
        timestamp: datetime
    ) -> float:
        """Close position and return realized PnL."""
        position.exit_time = timestamp
        position.exit_price = exit_price
        
        if position.direction == 'long':
            pnl = (exit_price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - exit_price) * position.size
        
        # Subtract exit fee
        pnl -= position.size * exit_price * self.fee_rate
        
        position.pnl = pnl
        position.return_pct = pnl / (position.entry_price * position.size)
        
        return pnl
    
    def _calculate_unrealized_pnl(
        self,
        position: Trade,
        current_price: float
    ) -> float:
        """Calculate unrealized PnL."""
        if position.direction == 'long':
            return (current_price - position.entry_price) * position.size
        else:
            return (position.entry_price - current_price) * position.size
    
    def _get_exit_price(self, price: float, direction: str) -> float:
        """Get exit price with slippage."""
        if direction == 'long':
            return price * (1 - self.slippage)
        else:
            return price * (1 + self.slippage)
    
    def _check_exit_conditions(
        self,
        position: Trade,
        candle: pd.Series,
        stop_loss_pct: Optional[float],
        take_profit_pct: Optional[float]
    ) -> Tuple[bool, str]:
        """Check if stop loss or take profit hit."""
        if stop_loss_pct:
            if position.direction == 'long':
                stop_price = position.entry_price * (1 - stop_loss_pct)
                if candle['low'] <= stop_price:
                    return True, "stop_loss"
            else:
                stop_price = position.entry_price * (1 + stop_loss_pct)
                if candle['high'] >= stop_price:
                    return True, "stop_loss"
        
        if take_profit_pct:
            if position.direction == 'long':
                tp_price = position.entry_price * (1 + take_profit_pct)
                if candle['high'] >= tp_price:
                    return True, "take_profit"
            else:
                tp_price = position.entry_price * (1 - take_profit_pct)
                if candle['low'] <= tp_price:
                    return True, "take_profit"
        
        return False, ""
    
    def compare_strategies(
        self,
        results: List[BacktestResult]
    ) -> pd.DataFrame:
        """Compare multiple backtest results."""
        comparison = []
        
        for r in results:
            comparison.append({
                "strategy": r.strategy_name,
                "return": r.metrics.get('total_return', 0),
                "sharpe": r.metrics.get('sharpe_ratio', 0),
                "max_dd": r.metrics.get('max_drawdown', 0),
                "win_rate": r.metrics.get('win_rate', 0),
                "profit_factor": r.metrics.get('profit_factor', 0),
                "trades": len(r.trades)
            })
        
        return pd.DataFrame(comparison).sort_values('sharpe', ascending=False)
