"""
Market API

Endpoints for market data with multi-timeframe support.
"""
import logging
from fastapi import APIRouter, HTTPException, Header, Query
from typing import Optional

from app.core.data_pipeline import DataPipeline
from app.core.feature_engine import FeatureEngine
from app.core.regime_detector import RegimeDetector
from app.services.binance_service import BinanceService
from app.config import Timeframes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market", tags=["market"])

from dataclasses import dataclass

@dataclass
class MarketComponents:
    data_pipeline: DataPipeline
    feature_engine: FeatureEngine
    regime_detector: RegimeDetector

_components: Optional[MarketComponents] = None

def get_market_components() -> MarketComponents:
    """Lazy load market components."""
    global _components
    if _components is None:
        _components = MarketComponents(
            data_pipeline=DataPipeline(),
            feature_engine=FeatureEngine(),
            regime_detector=RegimeDetector()
        )
    return _components


@router.get("/top-coins")
async def get_top_coins(
    limit: int = Query(default=100, ge=1, le=500),
    authorization: Optional[str] = Header(None)
):
    """Get top cryptocurrencies by volume."""
    try:
        binance = BinanceService()
        coins = binance.get_top_coins(limit)
        
        return {
            "success": True,
            "data": coins
        }
        
    except Exception as e:
        logger.error(f"Error fetching top coins: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch market data")


@router.get("/klines/{symbol}")
async def get_klines(
    symbol: str,
    interval: str = Query(default="1h", regex="^(15m|1h|4h)$"),
    limit: int = Query(default=500, ge=1, le=1000)
):
    """
    Get kline data for a symbol.
    
    Supports 15m, 1h, 4h intervals for visualization.
    Note: Model decisions use ONLY 1h data.
    """
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        if interval not in Timeframes.ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"Interval must be one of {Timeframes.ALLOWED}"
            )
        
        sys = get_market_components()
        candle_data = await sys.data_pipeline.get_candles(symbol, interval, limit)
        df = candle_data.df
        
        # Format for frontend charts
        data = []
        for timestamp, row in df.iterrows():
            data.append({
                "time": int(timestamp.timestamp()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume'])
            })
        
        return {
            "success": True,
            "data": data,
            "metadata": {
                "symbol": symbol,
                "interval": interval,
                "count": len(data),
                "is_decision_timeframe": interval == Timeframes.DECISION_1H
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching klines: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch kline data")


@router.get("/multi-timeframe/{symbol}")
async def get_multi_timeframe(symbol: str):
    """
    Get data for all timeframes.
    
    Returns 15m, 1h, 4h data for comprehensive analysis.
    """
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        sys = get_market_components()
        all_data = await sys.data_pipeline.get_multi_timeframe(symbol)
        
        result = {}
        for tf, candle_data in all_data.items():
            df = candle_data.df
            result[tf] = {
                "data": [
                    {
                        "time": int(ts.timestamp()),
                        "open": float(row['open']),
                        "high": float(row['high']),
                        "low": float(row['low']),
                        "close": float(row['close']),
                        "volume": float(row['volume'])
                    }
                    for ts, row in df.iterrows()
                ],
                "count": len(df),
                "latest_price": candle_data.latest_close
            }
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Error fetching multi-timeframe: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch data")


@router.get("/regime/{symbol}")
async def get_market_regime(symbol: str):
    """Get current market regime for a symbol."""
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        sys = get_market_components()
        # Get 1H data for regime detection
        candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=200)
        
        regime_state = sys.regime_detector.detect(candle_data.df)
        
        return {
            "success": True,
            "data": regime_state.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error detecting regime: {e}")
        raise HTTPException(status_code=500, detail="Failed to detect regime")


@router.get("/indicators/{symbol}")
async def get_indicators(symbol: str):
    """Get technical indicators for a symbol."""
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        
        sys = get_market_components()
        candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=200)
        features = sys.feature_engine.compute_features(candle_data.df, symbol)
        
        latest = features.get_latest()
        
        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "indicators": latest,
                "feature_groups": sys.feature_engine.get_feature_importance_groups()
            }
        }
        
    except Exception as e:
        logger.error(f"Error computing indicators: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute indicators")
