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

from app.core.system_state import get_state_manager, SystemState
from app.core.job_lock import get_lock_manager
from app.core.data_store import DataStore, InsufficientDataError

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
            try:
                # Acquire job lock
                lock = lock_manager.acquire(symbol, "training", "api")
                if not lock:
                    logger.warning(f"[{symbol}] Training skipped: locked by another job")
                    results[symbol] = {"status": "skipped", "reason": "locked_by_another_job"}
                    continue

                # Check system state (bypass if force=True)
                if not force and not state_manager.can_run_service(symbol, "training"):
                    current_state = state_manager.get_state(symbol)
                    logger.warning(
                        f"[{symbol}] Training skipped: invalid state "
                        f"{current_state.state.value}"
                    )
                    results[symbol] = {
                        "status": "skipped",
                        "reason": f"invalid_state:{current_state.state.value}"
                    }
                    continue

                # Transition to TRAINING state
                # When force=True, we may need to force the transition
                current_state = state_manager.get_state(symbol)
                if force and current_state.state != SystemState.READY_FOR_TRAINING:
                    # Force to READY_FOR_TRAINING first, then to TRAINING
                    logger.info(
                        f"[{symbol}] Force-transitioning from "
                        f"{current_state.state.value} → READY_FOR_TRAINING → TRAINING"
                    )
                    state_manager.force_state(
                        symbol=symbol,
                        new_state=SystemState.READY_FOR_TRAINING,
                        admin_key="ADMIN_FORCE_KEY",
                        reason="Force-training requested via API"
                    )

                state_manager.transition(
                    symbol=symbol,
                    new_state=SystemState.TRAINING,
                    actor="training_api",
                    reason="Training job started"
                )

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
                    state_manager.transition(
                        symbol, SystemState.COLLECTING_DATA,
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
                lock_manager.heartbeat(lock)
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
                    state_manager.transition(
                        symbol, SystemState.TRAINING_FAILED,
                        "training_api", str(e)
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
                    state_manager.transition(
                        symbol, SystemState.TRAINING_FAILED,
                        "training_api", "Invalid metrics"
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

                # Champion/Challenger evaluation
                current_champion = registry.get_champion(symbol)
                is_first_model = current_champion is None

                if is_first_model:
                    # AUTO-PROMOTE first model — no champion exists yet
                    logger.info(
                        f"[{symbol}] AUTO-PROMOTING first model to champion: "
                        f"{model_metadata.model_id}"
                    )
                    registry.promote_to_champion(model_metadata.model_id)
                    promoted = True
                    
                    # Cache model in memory for signal endpoint
                    try:
                        from app.models.model_cache import cache_model
                        from app.models.xgboost_model import XGBoostDecisionModel
                        cached = XGBoostDecisionModel.load(str(training_run.get_model_paths()["xgboost"]))
                        cache_model(symbol, cached)
                    except Exception as cache_err:
                        logger.warning(f"[{symbol}] Model cache failed: {cache_err}")
                else:
                    # Run champion/challenger gate
                    promotion_result = champion_challenger.evaluate_challenger(
                        challenger_id=model_metadata.model_id,
                        baseline_results=baseline_results,
                        multi_window_validated=False
                    )
                    promoted = promotion_result.status.value == "promoted"
                    logger.info(
                        f"[{symbol}] Champion/Challenger result: "
                        f"{promotion_result.status.value}"
                    )

                # Update system state based on promotion
                if promoted:
                    state_manager.transition(
                        symbol, SystemState.PROMOTED,
                        "training_api", "Model promoted to champion"
                    )
                else:
                    state_manager.transition(
                        symbol, SystemState.CHALLENGER_READY,
                        "training_api",
                        f"Challenger ready: {promotion_result.status.value}"
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
                    state_manager.transition(
                        symbol, SystemState.TRAINING_FAILED,
                        "training_api", str(e)
                    )
                except Exception:
                    pass  # State transition may also fail
            finally:
                if lock:
                    lock_manager.release(
                        lock,
                        success=results.get(symbol, {}).get("status") == "completed"
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
