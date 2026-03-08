"""
Performance Metrics

Standardized performance metric calculations for
backtesting, forward testing, and baseline comparison.
"""
import logging
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """
    Calculate trading performance metrics.
    
    All metrics are standardized to allow fair comparison
    between strategies, baselines, and model versions.
    """
    
    ANNUALIZATION_FACTOR = np.sqrt(24 * 365)  # Hourly to annual
    
    def calculate_all(
        self,
        equity_curve: pd.Series,
        trades: Optional[List[Dict]] = None
    ) -> Dict:
        """Calculate all performance metrics."""
        metrics = {}
        
        # Return metrics
        metrics['total_return'] = self.total_return(equity_curve)
        metrics['annualized_return'] = self.annualized_return(equity_curve)
        
        # Risk metrics
        metrics['volatility'] = self.volatility(equity_curve)
        metrics['max_drawdown'] = self.max_drawdown(equity_curve)
        metrics['avg_drawdown'] = self.avg_drawdown(equity_curve)
        
        # Risk-adjusted metrics
        metrics['sharpe_ratio'] = self.sharpe_ratio(equity_curve)
        metrics['sortino_ratio'] = self.sortino_ratio(equity_curve)
        metrics['calmar_ratio'] = self.calmar_ratio(equity_curve)
        
        # Trade-based metrics (if trades provided)
        if trades:
            metrics['trade_count'] = len(trades)
            metrics['win_rate'] = self.win_rate(trades)
            metrics['profit_factor'] = self.profit_factor(trades)
            metrics['avg_trade_return'] = self.avg_trade_return(trades)
            metrics['avg_win'] = self.avg_win(trades)
            metrics['avg_loss'] = self.avg_loss(trades)
            metrics['largest_win'] = self.largest_win(trades)
            metrics['largest_loss'] = self.largest_loss(trades)
            metrics['avg_holding_periods'] = self.avg_holding_periods(trades)
        
        return metrics
    
    def total_return(self, equity_curve: pd.Series) -> float:
        """Total return as a decimal (e.g., 0.15 = 15%)."""
        if len(equity_curve) < 2:
            return 0.0
        return (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
    
    def annualized_return(self, equity_curve: pd.Series) -> float:
        """Annualized return assuming hourly data."""
        total = self.total_return(equity_curve)
        periods = len(equity_curve)
        hours_per_year = 24 * 365
        
        if periods == 0:
            return 0.0
        
        return (1 + total) ** (hours_per_year / periods) - 1
    
    def volatility(self, equity_curve: pd.Series) -> float:
        """Annualized volatility of returns."""
        returns = equity_curve.pct_change().dropna()
        if len(returns) < 2:
            return 0.0
        return float(returns.std() * self.ANNUALIZATION_FACTOR)
    
    def max_drawdown(self, equity_curve: pd.Series) -> float:
        """Maximum drawdown as a positive decimal."""
        peak = equity_curve.expanding().max()
        drawdown = (peak - equity_curve) / peak
        return float(drawdown.max())
    
    def avg_drawdown(self, equity_curve: pd.Series) -> float:
        """Average drawdown."""
        peak = equity_curve.expanding().max()
        drawdown = (peak - equity_curve) / peak
        return float(drawdown.mean())
    
    def sharpe_ratio(
        self,
        equity_curve: pd.Series,
        risk_free_rate: float = 0.0
    ) -> float:
        """
        Sharpe ratio (annualized).
        
        Uses 0 risk-free rate by default for crypto.
        """
        returns = equity_curve.pct_change().dropna()
        if len(returns) < 2:
            return 0.0
        
        excess_return = returns.mean() - (risk_free_rate / (24 * 365))
        volatility = returns.std()
        
        if volatility == 0:
            return 0.0
        
        return float((excess_return / volatility) * self.ANNUALIZATION_FACTOR)
    
    def sortino_ratio(
        self,
        equity_curve: pd.Series,
        risk_free_rate: float = 0.0
    ) -> float:
        """Sortino ratio (only considers downside volatility)."""
        returns = equity_curve.pct_change().dropna()
        if len(returns) < 2:
            return 0.0
        
        excess_return = returns.mean() - (risk_free_rate / (24 * 365))
        downside_returns = returns[returns < 0]
        
        if len(downside_returns) == 0:
            return float('inf') if excess_return > 0 else 0.0
        
        downside_vol = downside_returns.std()
        if downside_vol == 0:
            return 0.0
        
        return float((excess_return / downside_vol) * self.ANNUALIZATION_FACTOR)
    
    def calmar_ratio(self, equity_curve: pd.Series) -> float:
        """Calmar ratio (annualized return / max drawdown)."""
        ann_return = self.annualized_return(equity_curve)
        max_dd = self.max_drawdown(equity_curve)
        
        if max_dd == 0:
            return float('inf') if ann_return > 0 else 0.0
        
        return ann_return / max_dd
    
    def win_rate(self, trades: List[Dict]) -> float:
        """Percentage of winning trades."""
        if not trades:
            return 0.0
        
        wins = sum(1 for t in trades if t.get('pnl', 0) > 0)
        return wins / len(trades)
    
    def profit_factor(self, trades: List[Dict]) -> float:
        """Gross profit / Gross loss."""
        gross_profit = sum(t['pnl'] for t in trades if t.get('pnl', 0) > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t.get('pnl', 0) < 0))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        
        return gross_profit / gross_loss
    
    def avg_trade_return(self, trades: List[Dict]) -> float:
        """Average return per trade."""
        if not trades:
            return 0.0
        
        returns = [t.get('return_pct', 0) for t in trades]
        return float(np.mean(returns))
    
    def avg_win(self, trades: List[Dict]) -> float:
        """Average winning trade return."""
        wins = [t.get('return_pct', 0) for t in trades if t.get('pnl', 0) > 0]
        return float(np.mean(wins)) if wins else 0.0
    
    def avg_loss(self, trades: List[Dict]) -> float:
        """Average losing trade return (as positive number)."""
        losses = [abs(t.get('return_pct', 0)) for t in trades if t.get('pnl', 0) < 0]
        return float(np.mean(losses)) if losses else 0.0
    
    def largest_win(self, trades: List[Dict]) -> float:
        """Largest winning trade PnL."""
        pnls = [t.get('pnl', 0) for t in trades if t.get('pnl', 0) > 0]
        return max(pnls) if pnls else 0.0
    
    def largest_loss(self, trades: List[Dict]) -> float:
        """Largest losing trade PnL (as positive number)."""
        pnls = [abs(t.get('pnl', 0)) for t in trades if t.get('pnl', 0) < 0]
        return max(pnls) if pnls else 0.0
    
    def avg_holding_periods(self, trades: List[Dict]) -> float:
        """Average trade duration in periods."""
        durations = []
        for t in trades:
            if 'entry_time' in t and 'exit_time' in t:
                duration = (t['exit_time'] - t['entry_time']).total_seconds() / 3600
                durations.append(duration)
        
        return float(np.mean(durations)) if durations else 0.0
    
    def expectancy(self, trades: List[Dict]) -> float:
        """
        Trading expectancy.
        
        E = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
        """
        wr = self.win_rate(trades)
        aw = self.avg_win(trades)
        al = self.avg_loss(trades)
        
        return (wr * aw) - ((1 - wr) * al)
    
    def recovery_factor(
        self,
        equity_curve: pd.Series,
        trades: List[Dict]
    ) -> float:
        """Net profit / Max drawdown."""
        net_profit = sum(t.get('pnl', 0) for t in trades)
        max_dd = self.max_drawdown(equity_curve) * equity_curve.iloc[0]
        
        if max_dd == 0:
            return float('inf') if net_profit > 0 else 0.0
        
        return net_profit / max_dd
