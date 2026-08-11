"""
Global in-memory model cache.

Since Cloud Run ephemeral filesystem loses model files on restart,
we keep trained models in memory. Combined with min-instances=1,
this ensures the signal endpoint can always access the trained model.
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Global cache: symbol -> XGBoostDecisionModel instance
_model_cache: Dict[str, any] = {}


def cache_model(symbol: str, model) -> None:
    """Store a trained model in the global cache."""
    _model_cache[symbol] = model
    logger.info(f"Cached model for {symbol} in memory")


def get_cached_model(symbol: str):
    """Get a cached model, returns None if not cached."""
    model = _model_cache.get(symbol)
    if model:
        logger.info(f"Using cached model for {symbol}")
    return model


def clear_cache(symbol: Optional[str] = None) -> None:
    """Clear cached models."""
    if symbol:
        _model_cache.pop(symbol, None)
    else:
        _model_cache.clear()
    logger.info(f"Model cache cleared for {symbol or 'all'}")
