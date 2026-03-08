"""
API module exports.
"""
from app.api.signals import router as signals_router
from app.api.market import router as market_router
from app.api.backtest import router as backtest_router
from app.api.admin import router as admin_router
from app.api.training import router as training_router

__all__ = [
    "signals_router",
    "market_router",
    "backtest_router",
    "admin_router",
    "training_router",
]
