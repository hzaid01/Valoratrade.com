"""
Scheduled forward-evaluation cycle (POST /api/training/forward-cycle).

Runs hourly from Cloud Scheduler:
  1. resolve_outcomes() for predictions past their holding period
  2. update_forward_metrics() for PENDING challengers and champions that
     have at least MIN_FORWARD_SAMPLES resolved predictions
  3. re-evaluate PENDING challengers through the champion/challenger gates
  4. move the per-symbol SystemState to match any promotion/rejection

Challengers collect forward predictions as SHADOW predictions logged by the
signals endpoint (never shown to users, no position size, no equity effect).
"""
import logging
import os
from typing import Callable, Dict, List, Optional

from app.core.system_state import SystemState

logger = logging.getLogger(__name__)

FORWARD_WINDOW_DAYS = 7


def min_forward_samples() -> int:
    try:
        return max(1, int(os.getenv("MIN_FORWARD_SAMPLES", "20")))
    except ValueError:
        return 20


def fetch_binance_price(symbol: str) -> float:
    import requests
    resp = requests.get(
        f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=5
    )
    resp.raise_for_status()
    price = float(resp.json()["price"])
    if price <= 0:
        raise ValueError(f"non-positive price {price}")
    return price


def run_forward_cycle(
    symbols: List[str],
    forward_engine,
    registry,
    champion_challenger,
    state_manager,
    price_fetcher: Callable[[str], float] = fetch_binance_price,
    on_resolved: Optional[Callable[[list], None]] = None,
    on_promoted: Optional[Callable[[str, object], None]] = None,
) -> Dict:
    from app.models.registry.model_registry import ModelStatus

    summary: Dict = {"prices": {}, "price_errors": {}, "resolved": 0,
                     "forward_metrics_updated": [], "evaluations": []}

    # 1. Resolve outcomes
    for symbol in symbols:
        try:
            summary["prices"][symbol] = price_fetcher(symbol)
        except Exception as e:
            summary["price_errors"][symbol] = str(e)
            logger.error(f"[forward-cycle] price fetch failed for {symbol}: {e}")
    resolved = forward_engine.resolve_outcomes(summary["prices"])
    summary["resolved"] = len(resolved)
    if on_resolved and resolved:
        try:
            on_resolved(resolved)
        except Exception as e:
            logger.error(f"[forward-cycle] on_resolved hook failed: {e}", exc_info=True)

    # 2. Forward metrics for pending challengers and champions
    need = min_forward_samples()
    for m in registry.list_models():
        if m.symbol not in symbols:
            continue
        if m.status not in (ModelStatus.PENDING.value, ModelStatus.CHAMPION.value):
            continue
        fm = forward_engine.get_forward_metrics(model_version=m.version, days=FORWARD_WINDOW_DAYS)
        if fm.resolved_predictions >= need:
            registry.update_forward_metrics(m.model_id, fm.to_dict())
            summary["forward_metrics_updated"].append(m.model_id)

    # 3. Re-evaluate pending challengers
    for symbol in symbols:
        for result in champion_challenger.reevaluate_pending(symbol=symbol):
            verdict = result.status.value
            summary["evaluations"].append({"model_id": result.challenger_id, "status": verdict})
            _sync_state(symbol, verdict, registry, state_manager)
            if verdict == "promoted" and on_promoted:
                try:
                    on_promoted(symbol, registry.get(result.challenger_id))
                except Exception as e:
                    logger.error(f"[forward-cycle] on_promoted hook failed: {e}")
    return summary


def _sync_state(symbol: str, verdict: str, registry, state_manager) -> None:
    """Move SystemState after a re-evaluation (only via valid transitions)."""
    current = state_manager.get_state(symbol).state
    target = None
    if verdict == "promoted":
        if current in (SystemState.FORWARD_TESTING, SystemState.CHALLENGER_READY,
                       SystemState.LIVE_DEGRADED):
            target = SystemState.PROMOTED
    elif verdict.startswith("rejected") and registry.get_champion(symbol) is None:
        if current in (SystemState.FORWARD_TESTING, SystemState.CHALLENGER_READY):
            target = SystemState.REJECTED
    if target and not state_manager.transition(
        symbol, target, "forward_cycle", f"re-evaluation: {verdict}"
    ):
        logger.error(f"[{symbol}] forward-cycle could not move {current.value} -> {target.value}")
