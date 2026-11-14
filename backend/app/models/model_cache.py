"""
Global in-memory model cache.

Since Cloud Run ephemeral filesystem loses model files on restart,
we keep trained models in memory. Combined with min-instances=1,
this ensures the signal endpoint can always access the trained model.

Entries remember WHICH model they hold (path + version). Previously the
cache was keyed by symbol only, so after a promotion on another instance
the signals process kept serving the old model under the new champion's
version label.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def normalize_path(path: Optional[str]) -> Optional[str]:
    return path.replace("\\", "/") if path else path


@dataclass
class CachedModel:
    model: Any
    path: Optional[str] = None
    version: Optional[str] = None


# Global cache: symbol -> CachedModel
_model_cache: Dict[str, CachedModel] = {}


def cache_model(symbol: str, model, path: Optional[str] = None, version: Optional[str] = None) -> None:
    """Store a model in the global cache, with the path/version it came from."""
    _model_cache[symbol] = CachedModel(model=model, path=normalize_path(path), version=version)
    logger.info(f"Cached model for {symbol} in memory (version={version}, path={path})")


def get_cached_entry(symbol: str) -> Optional[CachedModel]:
    return _model_cache.get(symbol)


def get_cached_model(symbol: str, expected_path: Optional[str] = None):
    """
    Get a cached model, or None. With ``expected_path``, only return it if
    it is that model (an entry with an unknown path never matches).
    """
    entry = _model_cache.get(symbol)
    if entry is None:
        return None
    if expected_path is not None and entry.path != normalize_path(expected_path):
        return None
    logger.info(f"Using cached model for {symbol}")
    return entry.model


def clear_cache(symbol: Optional[str] = None) -> None:
    """Clear cached models."""
    if symbol:
        _model_cache.pop(symbol, None)
    else:
        _model_cache.clear()
    logger.info(f"Model cache cleared for {symbol or 'all'}")
