"""
Training API

Endpoints for triggering model training.
Designed to be called by Cloud Scheduler every 15 minutes.

Integrates:
- System state machine for state enforcement
- Job locking for concurrency control
- Data store for accumulated candles
- Training run contract for artifact generation

Changes from v1:
- Auto-promotes first model to champion
- Force flag properly bypasses cooldown AND state checks
- Historical backfill on first ingestion
- Structured logging throughout
"""
import logging
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Tuple

from app.core.system_state import get_state_manager, SystemState, resume_state_after_training
from app.core.job_lock import get_lock_manager, JobOutcome, LockDenial
from app.core.data_store import DataStore, InsufficientDataError
from app.core.system_state import VALID_TRANSITIONS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/training", tags=["training"])

# Training control constants
MIN_CANDLES_FOR_TRAINING = 500
TRAINING_COOLDOWN_HOURS = 6
HISTORICAL_BACKFILL_CANDLES = 1000  # Fetch 1000 candles on first ingestion

# Track training status
_training_status = {
    "is_running": False,
    "last_run": None,
    "last_result": None,
    "run_count": 0,
    "last_success": None,
    "cooldown_until": None,
    "heartbeat": None
}


class TrainingRequest(BaseModel):
    """Training request body."""
    symbols: Optional[List[str]] = None
    force: bool = False


class IncrementalTrainingRequest(BaseModel):
    """Incremental training for continuous learning."""
    symbols: Optional[List[str]] = None


def first_model_gate_mode() -> str:
    """
    FIRST_MODEL_GATE=log_only|enforce (default log_only).
    log_only: first model is promoted even if the gates say pending/rejected
    (historical behaviour; marked provisional). enforce: gates decide.
    """
    import os
    raw = os.getenv("FIRST_MODEL_GATE", "log_only").strip().lower()
    if raw not in ("log_only", "enforce"):
        logger.warning(f"Unknown FIRST_MODEL_GATE={raw!r}; using log_only")
        return "log_only"
    return raw


def _cache_champion_model(symbol: str, xgboost_path: str) -> None:
    """
    Put the newly promoted champion in the in-memory cache used by signals.
    Previously only the first model was cached, so a later promotion kept
    serving the OLD cached model under the new champion's version. On load
    failure the old cache entry is kept (signals keep flowing) and logged.
    """
    try:
        from app.models.model_cache import cache_model
        from app.models.xgboost_model import XGBoostDecisionModel
        cache_model(symbol, XGBoostDecisionModel.load(xgboost_path))
    except Exception as cache_err:
        logger.error(f"[{symbol}] Model cache refresh failed, previous model stays cached: {cache_err}")


def _checked_transition(state_manager, symbol, new_state, actor, reason, metadata=None) -> bool:
    """
    End-of-run state transition whose failure cannot be undone (the model is
    already trained/registered). A False result is logged loudly with the
    state we were in, so stuck or racing symbols are visible.
    """
    ok = state_manager.transition(symbol, new_state, actor, reason, metadata=metadata)
    if not ok:
        current = state_manager.get_state(symbol)
        logger.error(
            f"[{symbol}] STATE TRANSITION REJECTED: wanted {new_state.value}, "
            f"state is {current.state.value} (actor={actor}, reason={reason})"
        )
    return ok


def _job_outcome(status: Optional[str]) -> JobOutcome:
    """
    Map a per-symbol training result status to a lock outcome.

    Only real failures count against the retry limit. "skipped" (invalid
    state, insufficient data, ...) is not a failure. A missing status means
    the job died before recording a result, which is treated as a failure.
    """
    if status == "completed":
        return JobOutcome.SUCCESS
    if status == "skipped":
        return JobOutcome.SKIPPED
    return JobOutcome.FAILED


async def run_training_job(symbols: List[str], force: bool = False):
    """
    Execute the training job with full infrastructure integration.

    Uses:
    - Job locking (prevents concurrent runs)
    - State machine (enforces valid states)
    - Data store (uses accumulated candles)
    - Training run contract (produces all artifacts)

    When force=True: bypasses cooldown, bypasses state checks,
    and auto-promotes first model to champion.
    """
    global _training_status

    lock_manager = get_lock_manager()
    state_manager = get_state_manager()
    data_store = DataStore()

    try:
        _training_status["is_running"] = True
        _training_status["last_run"] = datetime.utcnow().isoformat()
        _training_status["heartbeat"] = datetime.utcnow().isoformat()

        # Import here to avoid circular imports
        from app.core.feature_engine import FeatureEngine
        from app.models.training.trainer import ModelTrainer
        from app.models.training.training_run import TrainingRun
        from app.models.registry.model_registry import ModelRegistry
        from app.models.registry.champion_challenger import ChampionChallenger
        from app.evaluation.baselines import BaselineStrategies
        from app.governance.versioning import DatasetVersioning
        from app.governance.lineage import LineageTracker

        feature_engine = FeatureEngine()
        trainer = ModelTrainer()
        registry = ModelRegistry()
        baselines = BaselineStrategies()
        dataset_versioning = DatasetVersioning()
        lineage_tracker = LineageTracker()
        champion_challenger = ChampionChallenger(registry, baselines)

        results = {}

        for symbol in symbols:
            lock = None
            resume_state = None
            try:
                # Acquire job lock
                # force=True may bypass an exhausted retry count, never a live lock.
                acquired = lock_manager.try_acquire(
                    symbol, "training", "api", ignore_retry_limit=force
                )
                lock = acquired.lock
                if not lock:
                    denial = acquired.denial or LockDenial.HELD
                    logger.warning(f"[{symbol}] Training skipped: {denial.value}")
                    results[symbol] = {"status": "skipped", "reason": denial.value}
                    continue

                # Check system state (bypass if force=True)
                previous = state_manager.get_state(symbol)
                if not force and not state_manager.can_run_service(symbol, "training"):
                    logger.warning(
                        f"[{symbol}] Training skipped: invalid state "
                        f"{previous.state.value}"
                    )
                    results[symbol] = {
                        "status": "skipped",
                        "reason": f"invalid_state:{previous.state.value}"
                    }
                    continue

                # Where to return if a champion exists and is not replaced.
                resume_state = resume_state_after_training(previous)
                had_champion_at_start = registry.get_champion(symbol) is not None

                if SystemState.TRAINING not in VALID_TRANSITIONS.get(previous.state, set()):
                    # Only reachable with force=True (permissions guarantee
                    # TRAINING is valid otherwise). Explicit, audited override
                    # instead of the old hardcoded ADMIN_FORCE_KEY.
                    state_manager.override(
                        symbol, SystemState.TRAINING,
                        actor="training_api_force",
                        reason=f"Force-training requested via API from {previous.state.value}",
                        metadata={"resume_state": resume_state.value}
                    )
                elif not state_manager.transition(
                    symbol=symbol,
                    new_state=SystemState.TRAINING,
                    actor="training_api",
                    reason="Training job started",
                    metadata={"resume_state": resume_state.value}
                ):
                    # State changed under us (another instance/job). Do not
                    # train from an unexpected state.
                    results[symbol] = {
                        "status": "skipped",
                        "reason": "state_transition_rejected"
                    }
                    continue

                logger.info(f"{'='*50}")
                logger.info(f"[{symbol}] TRAINING STARTED")
                logger.info(f"{'='*50}")
                training_start = datetime.utcnow()

                # Create training run
                run_id = f"{symbol}_v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                training_run = TrainingRun(run_id=run_id, symbol=symbol)

                # Get training data from data store (accumulated candles)
                try:
                    df = data_store.get_training_data(symbol, "1h", MIN_CANDLES_FOR_TRAINING)
                    logger.info(f"[{symbol}] Loaded {len(df)} candles for training")
                except InsufficientDataError as e:
                    training_run.mark_failed(str(e), "data_loading")
                    logger.error(f"[{symbol}] Insufficient data: {e}")
                    results[symbol] = {
                        "status": "skipped",
                        "reason": "insufficient_data",
                        "error": str(e)
                    }
                    # With a champion serving, keep serving; otherwise go back
                    # to collecting data (TRAINING -> COLLECTING_DATA is valid).
                    _checked_transition(
                        state_manager, symbol,
                        resume_state if had_champion_at_start else SystemState.COLLECTING_DATA,
                        "training_api", "Insufficient data"
                    )
                    continue

                # Create manifest BEFORE training
                dataset_snapshot_id = training_run.compute_dataset_snapshot_id(df)
                training_run.create_manifest(
                    dataset_snapshot_id=dataset_snapshot_id,
                    feature_version="1.0.0",
                    training_data_rows=len(df)
                )
                training_run.seal_manifest()

                # Update heartbeat
                if not lock_manager.heartbeat(lock):
                    # Our lock went stale and another job took it over; stop
                    # rather than train concurrently with the new owner.
                    raise RuntimeError("Job lock lost (taken over by another job)")
                _training_status["heartbeat"] = datetime.utcnow().isoformat()

                # Compute features
                feature_engine.compute_features(df, symbol)

                # Run baselines (STORED for comparison)
                baseline_results = baselines.run_all(df)
                logger.info(f"[{symbol}] Baselines computed")

                # Train new model
                try:
                    training_result = trainer.train(
                        df=df,
                        symbol=symbol,
                        model_version=run_id
                    )
                    logger.info(
                        f"[{symbol}] Model training completed: "
                        f"metrics={training_result.metrics}"
                    )
                except Exception as e:
                    training_run.mark_failed(str(e), "model_training")
                    logger.error(f"[{symbol}] Training FAILED: {e}", exc_info=True)
                    _checked_transition(
                        state_manager, symbol, SystemState.TRAINING_FAILED,
                        "training_api", str(e),
                        metadata={"resume_state": resume_state.value} if resume_state else None
                    )
                    results[symbol] = {
                        "status": "failed", "error": str(e),
                        "stage": "training"
                    }
                    continue

                # Finalize training run (saves metrics, baselines, forward placeholder)
                run_result = training_run.finalize(
                    training_metrics=training_result.metrics,
                    validation_metrics=training_result.validation_metrics,
                    baseline_results=baseline_results
                )

                if run_result.status == "invalid":
                    logger.error(f"[{symbol}] Training metrics invalid: {run_result.errors}")
                    _checked_transition(
                        state_manager, symbol, SystemState.TRAINING_FAILED,
                        "training_api", "Invalid metrics",
                        metadata={"resume_state": resume_state.value} if resume_state else None
                    )
                    results[symbol] = {"status": "invalid", "errors": run_result.errors}
                    continue

                # Register model
                model_metadata = registry.register(
                    version=run_id,
                    symbol=symbol,
                    patch_tst_path=str(training_run.get_model_paths()["patchtst"]),
                    xgboost_path=str(training_run.get_model_paths()["xgboost"]),
                    dataset_version=dataset_snapshot_id,
                    feature_version="1.0.0",
                    training_snapshot=dataset_snapshot_id,
                    training_metrics=training_result.metrics,
                    validation_metrics=training_result.validation_metrics
                )
                logger.info(f"[{symbol}] Model registered: {model_metadata.model_id}")

                # Record lineage
                lineage_tracker.record_training(
                    model_id=model_metadata.model_id,
                    model_version=run_id,
                    dataset_version=dataset_snapshot_id,
                    feature_version="1.0.0",
                    training_snapshot_id=dataset_snapshot_id,
                    training_params=training_result.config,
                    training_metrics=training_result.metrics,
                    validation_metrics=training_result.validation_metrics
                )

                # Champion/Challenger evaluation. The first model goes through
                # the same gates (promote=False), then FIRST_MODEL_GATE decides.
                current_champion = registry.get_champion(symbol)
                is_first_model = current_champion is None

                promotion_result = champion_challenger.evaluate_challenger(
                    challenger_id=model_metadata.model_id,
                    baseline_results=baseline_results,
                    multi_window_validated=False,
                    promote=not is_first_model
                )
                verdict = promotion_result.status.value
                logger.info(f"[{symbol}] Champion/Challenger result: {verdict}")

                if is_first_model:
                    if verdict == "promoted":
                        promoted = registry.promote_to_champion(
                            model_metadata.model_id, actor="training_api",
                            reason="first model passed promotion gates"
                        )
                    elif first_model_gate_mode() == "enforce":
                        promoted = False
                        logger.warning(
                            f"[{symbol}] FIRST_MODEL_GATE enforce: first model not promoted ({verdict})"
                        )
                    else:
                        # log_only (default): keep the historical behaviour of
                        # auto-promoting, so a symbol is never left without
                        # signals, but mark it provisional.
                        logger.warning(
                            f"[{symbol}] FIRST_MODEL_GATE log_only: promoting first model "
                            f"{model_metadata.model_id} despite gate result {verdict} (provisional)"
                        )
                        promoted = registry.promote_to_champion(
                            model_metadata.model_id, actor="training_api",
                            reason=f"first model auto-promoted (gate: {verdict})",
                            provisional=True
                        )
                else:
                    promoted = verdict == "promoted"

                if promoted:
                    _cache_champion_model(symbol, str(training_run.get_model_paths()["xgboost"]))

                # Update system state based on promotion
                if promoted:
                    _checked_transition(
                        state_manager, symbol, SystemState.PROMOTED,
                        "training_api", "Model promoted to champion"
                    )
                elif registry.get_champion(symbol) is not None:
                    # Existing champion keeps serving; challenger stays in the
                    # registry for later re-evaluation.
                    _checked_transition(
                        state_manager, symbol, resume_state, "training_api",
                        f"Champion retained; challenger {promotion_result.status.value}"
                    )
                elif promotion_result.status.value.startswith("rejected"):
                    _checked_transition(
                        state_manager, symbol, SystemState.REJECTED, "training_api",
                        f"Challenger rejected: {promotion_result.status.value}"
                    )
                else:
                    _checked_transition(
                        state_manager, symbol, SystemState.FORWARD_TESTING, "training_api",
                        f"Challenger pending: {promotion_result.status.value}"
                    )

                training_duration = (datetime.utcnow() - training_start).total_seconds()
                results[symbol] = {
                    "status": "completed",
                    "run_id": run_id,
                    "model_id": model_metadata.model_id,
                    "promoted": promoted,
                    "training_samples": (
                        training_result.training_samples
                        if hasattr(training_result, 'training_samples')
                        else len(df)
                    ),
                    "metrics": training_result.metrics,
                    "duration_seconds": round(training_duration, 1)
                }

                logger.info(f"{'='*50}")
                logger.info(
                    f"[{symbol}] TRAINING COMPLETED in {training_duration:.1f}s "
                    f"| promoted={promoted}"
                )
                logger.info(f"{'='*50}")

            except Exception as e:
                logger.error(f"[{symbol}] Training FAILED: {e}", exc_info=True)
                results[symbol] = {"status": "failed", "error": str(e)}
                try:
                    _checked_transition(
                        state_manager, symbol, SystemState.TRAINING_FAILED,
                        "training_api", str(e),
                        metadata={"resume_state": resume_state.value} if resume_state else None
                    )
                except Exception:
                    pass  # State transition may also fail
            finally:
                if lock:
                    lock_manager.release(
                        lock, _job_outcome(results.get(symbol, {}).get("status"))
                    )

        _training_status["last_result"] = results
        _training_status["run_count"] += 1
        _training_status["last_success"] = datetime.utcnow().isoformat()
        _training_status["cooldown_until"] = (
            datetime.utcnow() + timedelta(hours=TRAINING_COOLDOWN_HOURS)
        ).isoformat()

        return results

    except Exception as e:
        logger.error(f"Training job FAILED: {e}", exc_info=True)
        _training_status["last_result"] = {"error": str(e)}
        raise
    finally:
        _training_status["is_running"] = False


async def check_training_preconditions(symbols: List[str]) -> Tuple[bool, str]:
    """
    Check if training should run.
    Returns (should_run, reason).
    """
    # Check cooldown
    if _training_status["cooldown_until"]:
        cooldown = datetime.fromisoformat(_training_status["cooldown_until"])
        if datetime.utcnow() < cooldown:
            return False, f"In cooldown until {cooldown.isoformat()}"

    # Check if already running
    if _training_status["is_running"]:
        return False, "Training already in progress"

    # Check data availability
    data_store = DataStore()
    for symbol in symbols:
        stats = data_store.get_data_stats(symbol)
        if stats["candle_count"] < MIN_CANDLES_FOR_TRAINING:
            return False, f"{symbol} has {stats['candle_count']} candles, need {MIN_CANDLES_FOR_TRAINING}"

    return True, "All preconditions met"


@router.post("/trigger")
async def trigger_training(
    request: TrainingRequest,
    background_tasks: BackgroundTasks
):
    """
    Trigger model training with precondition checks.

    Called by Cloud Scheduler or manually.
    Runs in background to avoid timeout.

    Use force=True to bypass cooldown and state checks.
    """
    symbols = request.symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # Skip precondition check if force=True
    if not request.force:
        can_run, reason = await check_training_preconditions(symbols)
        if not can_run:
            return {
                "success": False,
                "message": f"Training skipped: {reason}",
                "status": _training_status
            }

    # Check for concurrent runs
    if _training_status["is_running"]:
        return {
            "success": False,
            "message": "Training already in progress",
            "status": _training_status
        }

    logger.info(f"Training triggered for {symbols} (force={request.force})")

    # Run training in background
    background_tasks.add_task(run_training_job, symbols, request.force)

    return {
        "success": True,
        "message": f"Training triggered for {symbols}",
        "force": request.force,
        "status": "started"
    }


@router.get("/status")
async def get_training_status():
    """Get current training status."""
    return {
        "success": True,
        "data": _training_status
    }


@router.post("/run-sync")
async def run_training_sync(request: TrainingRequest):
    """
    Run training synchronously (for Cloud Run Jobs).

    Warning: May timeout for large training jobs.
    """
    if _training_status["is_running"]:
        raise HTTPException(status_code=409, detail="Training already in progress")

    symbols = request.symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    try:
        results = await run_training_job(symbols, request.force)
        return {
            "success": True,
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest-data")
async def ingest_data(symbols: Optional[List[str]] = None):
    """
    Ingest latest candles from Binance.

    Should run every 15 minutes via Cloud Scheduler.
    This is the DATA ACCUMULATION job — lightweight, frequent.

    On first ingestion (no existing data), fetches up to 1000 historical
    candles to bootstrap the dataset faster.
    """
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    data_store = DataStore()
    results = {}

    for symbol in symbols:
        try:
            # Check if we need more data for training
            stats = data_store.get_data_stats(symbol)
            needs_backfill = stats["candle_count"] < DataStore.MIN_CANDLES_FOR_TRAINING

            # Always ingest latest candles first
            result = await data_store.ingest_latest_candles(symbol, "1h", 100)
            
            # If below training threshold, also backfill older historical data
            if needs_backfill:
                logger.info(
                    f"[{symbol}] Below training threshold ({stats['candle_count']}/{DataStore.MIN_CANDLES_FOR_TRAINING}) — "
                    f"backfilling historical data"
                )
                backfill_result = await data_store.backfill_historical(
                    symbol, "1h", HISTORICAL_BACKFILL_CANDLES
                )
                # Merge results
                result["new_candles"] = result.get("new_candles", 0) + backfill_result.get("new_candles", 0)
                result["total_stored"] = backfill_result.get("total_stored", result.get("total_stored", 0))
                result["backfill"] = backfill_result
            
            results[symbol] = result

            # Log candle counts
            updated_stats = data_store.get_data_stats(symbol)
            logger.info(
                f"[{symbol}] Ingestion complete: "
                f"new={result.get('new_candles', 0)}, "
                f"total={updated_stats['candle_count']}, "
                f"ready_for_training={updated_stats['sufficient_for_training']}"
            )

        except Exception as e:
            logger.error(f"[{symbol}] Ingestion FAILED: {e}", exc_info=True)
            results[symbol] = {"error": str(e)}

    return {
        "success": True,
        "message": "Data ingestion completed",
        "data": results
    }


@router.get("/data-stats")
async def get_data_stats(symbols: Optional[List[str]] = None):
    """
    Get data accumulation statistics.

    Shows candle counts and readiness for training.
    """
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    data_store = DataStore()
    stats = {}

    for symbol in symbols:
        try:
            stats[symbol] = data_store.get_data_stats(symbol)
        except Exception as e:
            stats[symbol] = {"error": str(e)}

    return {
        "success": True,
        "data": stats
    }


@router.get("/system-state")
async def get_system_states(symbols: Optional[List[str]] = None):
    """
    Get current system state for each symbol.
    """
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    state_manager = get_state_manager()
    states = {}

    for symbol in symbols:
        try:
            snapshot = state_manager.get_state(symbol)
            states[symbol] = {
                "state": snapshot.state.value,
                "updated_at": snapshot.updated_at.isoformat(),
                "updated_by": snapshot.updated_by,
                "reason": snapshot.reason
            }
        except Exception as e:
            states[symbol] = {"error": str(e)}

    return {
        "success": True,
        "data": states
    }


@router.post("/forward-cycle")
async def forward_cycle(symbols: Optional[List[str]] = None):
    """
    Resolve matured predictions, refresh forward metrics and re-evaluate
    PENDING challengers. Intended for Cloud Scheduler (hourly).
    """
    from app.evaluation.forward_cycle import run_forward_cycle
    from app.evaluation.forward_engine import ForwardEngine
    from app.evaluation.baselines import BaselineStrategies
    from app.models.registry.model_registry import ModelRegistry
    from app.models.registry.champion_challenger import ChampionChallenger

    symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    lock_manager = get_lock_manager()
    # Hourly and idempotent: failures are logged/counted but must never lock
    # the cycle out permanently, so the retry limit is not applied.
    acquired = lock_manager.try_acquire("ALL", "forward_cycle", "api", ignore_retry_limit=True)
    if not acquired.lock:
        return {"success": False, "message": f"Forward cycle skipped: {acquired.denial.value}"}

    outcome = JobOutcome.FAILED
    try:
        registry = ModelRegistry()
        summary = run_forward_cycle(
            symbols=symbols,
            forward_engine=ForwardEngine(),
            registry=registry,
            champion_challenger=ChampionChallenger(registry, BaselineStrategies()),
            state_manager=get_state_manager(),
            on_promoted=lambda sym, m: _cache_champion_model(sym, m.xgboost_path),
        )
        outcome = JobOutcome.SUCCESS
        return {"success": True, "data": summary}
    except Exception as e:
        logger.error(f"Forward cycle FAILED: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Forward cycle failed: {e}")
    finally:
        lock_manager.release(acquired.lock, outcome)
