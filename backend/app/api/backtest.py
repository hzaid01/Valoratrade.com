"""
Backtest API

Endpoints for backtesting strategies and models.
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import pandas as pd

from app.core.data_pipeline import DataPipeline
from app.evaluation.backtest_engine import BacktestEngine
from app.evaluation.baselines import BaselineStrategies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])

from dataclasses import dataclass

@dataclass
class BacktestComponents:
    data_pipeline: DataPipeline
    backtest_engine: BacktestEngine
    baseline_strategies: BaselineStrategies

_components: Optional[BacktestComponents] = None

def get_backtest_components() -> BacktestComponents:
    """Lazy load backtest components."""
    global _components
    if _components is None:
        _components = BacktestComponents(
            data_pipeline=DataPipeline(),
            backtest_engine=BacktestEngine(),
            baseline_strategies=BaselineStrategies()
        )
    return _components


class BacktestRequest(BaseModel):
    """Backtest request body."""
    symbol: str
    strategy: str = "model"  # 'model' or baseline name
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    stop_loss_pct: Optional[float] = 0.02
    take_profit_pct: Optional[float] = 0.04


@router.post("/run")
async def run_backtest(request: BacktestRequest):
    """
    Run a backtest.
    
    Supports model strategy or baseline strategies.
    """
    try:
        sys = get_backtest_components()
        symbol = request.symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        # Fetch data
        candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=1000)
        df = candle_data.df
        
        if df.empty:
            raise HTTPException(status_code=404, detail="No data available")
        
        # Generate signals based on strategy
        if request.strategy == "model":
            # TODO: Load model and generate signals
            # For now, use simple momentum
            signals = pd.Series(0, index=df.index)
            returns = df['close'].pct_change()
            signals[returns > 0.01] = 1
            signals[returns < -0.01] = -1
        else:
            # Run baseline strategy
            baseline_result = sys.baseline_strategies.run_strategy(
                df,
                request.strategy
            )
            return {
                "success": True,
                "data": baseline_result.to_dict()
            }
        
        # Run backtest
        result = sys.backtest_engine.run(
            df=df,
            signals=signals,
            symbol=symbol,
            strategy_name=request.strategy,
            stop_loss_pct=request.stop_loss_pct,
            take_profit_pct=request.take_profit_pct
        )
        
        return {
            "success": True,
            "data": result.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(status_code=500, detail="Backtest failed")


@router.get("/baselines/{symbol}")
async def run_all_baselines(symbol: str):
    """Run all baseline strategies for comparison."""
    try:
        sys = get_backtest_components()
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=1000)
        df = candle_data.df
        
        if df.empty:
            raise HTTPException(status_code=404, detail="No data available")
        
        results = sys.baseline_strategies.run_all(df)
        
        return {
            "success": True,
            "data": {
                name: result.to_dict()
                for name, result in results.items()
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Baseline error: {e}")
        raise HTTPException(status_code=500, detail="Baseline comparison failed")


@router.get("/compare/{symbol}")
async def compare_strategies(symbol: str):
    """Compare model against baselines."""
    try:
        sys = get_backtest_components()
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=1000)
        df = candle_data.df
        
        # Run baselines
        baseline_results = sys.baseline_strategies.run_all(df)
        
        # Simple model comparison (placeholder)
        model_metrics = {
            "sharpe_ratio": 0.5,  # Placeholder
            "total_return": 0.0,
            "max_drawdown": 0.0
        }
        
        comparison = sys.baseline_strategies.compare_to_model(model_metrics, baseline_results)
        
        return {
            "success": True,
            "data": comparison
        }
        
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(status_code=500, detail="Comparison failed")
