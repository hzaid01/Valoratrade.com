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
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from app.core.system_state import SystemState

logger = logging.getLogger(__name__)

FORWARD_WINDOW_DAYS = 7


def min_forward_samples() -> int:
    try:
        return max(1, int(os.getenv("MIN_FORWARD_SAMPLES", "20")))
    except ValueError:
        return 20


def demotion_mode() -> str:
    """DEMOTION_MODE=log_only|enforce (default log_only)."""
    raw = os.getenv("DEMOTION_MODE", "log_only").strip().lower()
    if raw not in ("log_only", "enforce"):
        logger.warning(f"Unknown DEMOTION_MODE={raw!r}; using log_only")
        return "log_only"
    return raw


def fetch_binance_close_at(symbol: str, when: datetime) -> Optional[float]:
    """
    Close of the 1h Binance candle containing ``when`` (the prediction's
    maturity), so a late resolver run still uses the price at maturity.
    Returns None if that candle has not closed yet / is unavailable.
    """
    import requests
    hour = when.replace(minute=0, second=0, microsecond=0)
    start_ms = int(hour.replace(tzinfo=timezone.utc).timestamp() * 1000)
    resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": "1h", "startTime": start_ms, "limit": 1},
        timeout=5,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    open_time, close_time, close = int(rows[0][0]), int(rows[0][6]), float(rows[0][4])
    if open_time != start_ms:
        return None
    if close_time > int(datetime.now(timezone.utc).timestamp() * 1000):
        return None  # candle still open
    return close if close > 0 else None


def apply_resolved_to_equity(controller, resolved) -> int:
    """
    Feed resolved, served (non-shadow) predictions with a position size
    into the capital controller as closed paper trades. Returns count.
    """
    applied = 0
    for p in resolved:
        if p.shadow or not p.position_size or p.direction not in ("long", "short"):
            continue
        try:
            controller.record_closed_trade(
                symbol=p.symbol, direction=p.direction, entry_price=p.entry_price,
                exit_price=p.exit_price, size=p.position_size, entry_time=p.timestamp,
            )
            applied += 1
        except Exception as e:
            logger.error(f"[forward-cycle] equity update failed for {p.id}: {e}", exc_info=True)
    return applied


def run_forward_cycle(
    symbols: List[str],
    forward_engine,
    registry,
    champion_challenger,
    state_manager,
    price_at: Callable[[str, datetime], Optional[float]] = fetch_binance_close_at,
    on_resolved: Optional[Callable[[list], None]] = None,
    on_promoted: Optional[Callable[[str, object], None]] = None,
) -> Dict:
    from app.models.registry.model_registry import ModelStatus

    summary: Dict = {"price_errors": {}, "resolved": 0,
                     "forward_metrics_updated": [], "evaluations": []}

    # 1. Resolve matured predictions at their maturity price
    def _price(symbol, when):
        try:
            return price_at(symbol, when)
        except Exception as e:
            summary["price_errors"][symbol] = str(e)
            logger.error(f"[forward-cycle] price fetch failed for {symbol}: {e}")
            return None

    resolved = forward_engine.resolve_pending(_price)
    summary["resolved"] = len(resolved)
    if on_resolved and resolved:
        try:
            on_resolved(resolved)
        except Exception as e:
            logger.error(f"[forward-cycle] on_resolved hook failed: {e}", exc_info=True)

    # 2. Forward metrics for pending challengers and champions
    need = min_forward_samples()
    champion_metrics: Dict[str, Dict] = {}
    for m in registry.list_models():
        if m.symbol not in symbols:
            continue
        if m.status not in (ModelStatus.PENDING.value, ModelStatus.CHAMPION.value):
            continue
        fm = forward_engine.get_forward_metrics(model_version=m.version, days=FORWARD_WINDOW_DAYS)
        if fm.resolved_predictions >= need:
            fm_dict = fm.to_dict()
            registry.update_forward_metrics(m.model_id, fm_dict)
            summary["forward_metrics_updated"].append(m.model_id)
            if m.status == ModelStatus.CHAMPION.value:
                champion_metrics[m.symbol] = fm_dict

    # 2b. Demotion of degraded champions (DEMOTION_MODE, default log_only)
    summary["demotions"] = []
    if champion_metrics:
        from app.models.registry.demotion import ModelDemotion
        demotion = ModelDemotion(registry=registry, state_manager=state_manager)
        mode = demotion_mode()
        for symbol, fm_dict in champion_metrics.items():
            # Map forward-engine metrics onto the demotion inputs. Only
            # accuracy is measured forward today; drawdown/streak are not.
            reason = demotion.evaluate(symbol, {"rolling_accuracy": fm_dict.get("accuracy")})
            if not reason:
                continue
            champion = registry.get_champion(symbol)
            if mode != "enforce":
                logger.warning(
                    f"DEMOTION_MODE log_only: WOULD HAVE DEMOTED {champion.model_id}: {reason}"
                )
                summary["demotions"].append({"model_id": champion.model_id, "reason": reason, "applied": False})
                continue
            event = demotion._trigger_demotion(champion.model_id, symbol, reason, fm_dict)
            summary["demotions"].append({
                "model_id": champion.model_id, "reason": reason, "applied": True,
                "rolled_back_to": event.rolled_back_to,
            })
            if event.rolled_back_to and on_promoted:
                try:
                    on_promoted(symbol, registry.get(event.rolled_back_to))
                except Exception as e:
                    logger.error(f"[forward-cycle] on_promoted after rollback failed: {e}")

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
