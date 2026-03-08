"""
Signals API

Endpoints for trading signal generation and history.
Returns explicit SYSTEM_NOT_READY when no champion model exists.
"""
import logging
from fastapi import APIRouter, HTTPException, Header, Depends
from typing import Optional
from dataclasses import dataclass
from app.core.data_pipeline import DataPipeline, DataPipelineError
from app.core.feature_engine import FeatureEngine
from app.core.regime_detector import RegimeDetector
from app.capital.controller import CapitalController
from app.strategy.signal_generator import SignalGenerator, SignalType, TradingSignal
from app.strategy.trade_levels import TradeLevelCalculator
from app.strategy.position_sizer import PositionSizer
from app.evaluation.forward_engine import ForwardEngine
from app.models.registry.model_registry import ModelRegistry
from app.config import Timeframes
from app.models.xgboost_model import XGBoostDecisionModel, ModelPrediction
from app.firebase_config import verify_firebase_token
from app.core.regime_detector import MarketRegime


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/signals", tags=["signals"])

@dataclass
class SystemComponents:
    data_pipeline: DataPipeline
    feature_engine: FeatureEngine
    regime_detector: RegimeDetector
    capital_controller: CapitalController
    signal_generator: SignalGenerator
    trade_calculator: TradeLevelCalculator
    position_sizer: PositionSizer
    forward_engine: ForwardEngine
    model_registry: ModelRegistry

_components: Optional[SystemComponents] = None

def get_sys_components() -> SystemComponents:
    """Lazy load system components."""
    global _components
    if _components is None:
        logger.info("Initializing system components...")
        capital_controller = CapitalController()
        _components = SystemComponents(
            data_pipeline=DataPipeline(),
            feature_engine=FeatureEngine(),
            regime_detector=RegimeDetector(),
            capital_controller=capital_controller,
            signal_generator=SignalGenerator(capital_controller),
            trade_calculator=TradeLevelCalculator(),
            position_sizer=PositionSizer(),
            forward_engine=ForwardEngine(),
            model_registry=ModelRegistry()
        )
        logger.info("System components initialized.")
    return _components


async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Extract user from Firebase token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    try:
        return verify_firebase_token(token)
    except Exception:
        return None


def _system_not_ready_response(symbol: str, reason: str) -> dict:
    """Return a structured SYSTEM_NOT_READY response."""
    return {
        "success": True,
        "data": {
            "signal": "system_not_ready",
            "symbol": symbol,
            "confidence": 0.0,
            "predictions": {
                "prob_up": 0.0,
                "prob_down": 0.0,
                "expected_return": 0.0
            },
            "context": {
                "regime": "unknown",
                "volatility_score": 0.0
            },
            "trade": {
                "entry_price": 0.0,
                "stop_loss": None,
                "take_profit": None,
                "position_size": None
            },
            "metadata": {
                "model_version": "none",
                "generated_at": None,
                "filters_passed": {},
                "system_status": reason
            }
        }
    }


@router.get("/{symbol}")
async def get_signal(
    symbol: str,
    user: Optional[dict] = Depends(get_current_user)
):
    """
    Get trading signal for a symbol.
    Returns SYSTEM_NOT_READY if no champion model exists.
    """
    try:
        sys = get_sys_components()

        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        # Step 1: Get decision timeframe data
        try:
            candle_data = await sys.data_pipeline.get_decision_data(symbol, limit=500)
        except DataPipelineError as e:
            logger.error(f"Data pipeline error for {symbol}: {e}")
            raise HTTPException(status_code=503, detail=f"Data unavailable: {e}")

        df = candle_data.df

        if df.empty:
            raise HTTPException(status_code=404, detail="No data available")

        current_price = candle_data.latest_close

        # Step 2: Compute features
        features = sys.feature_engine.compute_features(df, symbol, Timeframes.DECISION_1H)

        # Step 3: Detect regime
        regime_state = sys.regime_detector.detect(df)

        # Step 4: Get champion model
        champion = sys.model_registry.get_champion(symbol)

        if champion:
            # Load and run model — try in-memory cache first, then disk
            try:
                from app.models.model_cache import get_cached_model
                model = get_cached_model(symbol)
                if model is None:
                    model = XGBoostDecisionModel.load(champion.xgboost_path)
                feature_array = features.to_numpy()[-1:].reshape(1, -1)
                prediction = model.predict_single(feature_array)
                logger.info(
                    f"Model prediction for {symbol}: "
                    f"prob_up={prediction.prob_up:.4f}, "
                    f"prob_down={prediction.prob_down:.4f}, "
                    f"expected_return={prediction.expected_return:.6f}, "
                    f"confidence={prediction.confidence:.4f}"
                )
            except Exception as e:
                logger.error(f"Model prediction failed for {symbol}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=503,
                    detail=f"Model prediction failed: {e}"
                )
        else:
            # No champion model — return explicit SYSTEM_NOT_READY
            logger.warning(
                f"No champion model for {symbol}. "
                f"System state: model_registry is empty. "
                f"Run training first: POST /api/training/trigger"
            )
            return _system_not_ready_response(
                symbol,
                "No champion model exists. Trigger training via POST /api/training/trigger"
            )

        # Step 5: Generate signal
        signal = sys.signal_generator.generate(
            symbol=symbol,
            prediction=prediction,
            regime_state=regime_state,
            current_price=current_price,
            model_version=champion.version if champion else "default"
        )

        # Step 6: Calculate trade levels if signal
        if signal.signal_type.value != "no_trade":
            support, resistance = sys.trade_calculator.calculate_support_resistance(df)
            levels = sys.trade_calculator.calculate(
                df=df,
                entry_price=current_price,
                direction=signal.signal_type.value,
                support=support,
                resistance=resistance
            )
            signal.stop_loss = levels.stop_loss
            signal.take_profit = levels.take_profit_1

            # Calculate position size
            pos_size = sys.position_sizer.calculate(
                available_capital=sys.capital_controller.equity_state.equity,
                entry_price=current_price,
                stop_loss_price=levels.stop_loss,
                confidence=signal.confidence,
                volatility_score=signal.volatility_score,
                regime=regime_state.regime,
                current_drawdown_pct=sys.capital_controller.equity_state.drawdown_pct
            )
            signal.position_size = pos_size.size

        # Step 7: Log prediction for forward engine
        sys.forward_engine.log_prediction(
            symbol=symbol,
            direction=signal.signal_type.value,
            confidence=signal.confidence,
            entry_price=current_price,
            model_version=signal.model_version
        )

        return {
            "success": True,
            "data": signal.to_dict()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating signal for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Signal generation failed")


@router.get("/history/{symbol}")
async def get_signal_history(
    symbol: str,
    limit: int = 50,
    user: Optional[dict] = Depends(get_current_user)
):
    """Get recent signal history for a symbol."""
    try:
        sys = get_sys_components()
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        try:
            history = sys.forward_engine.get_prediction_history(symbol, limit)
        except Exception:
            history = []

        return {
            "success": True,
            "data": history
        }

    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")


@router.get("/forward/metrics")
async def get_forward_metrics(
    model_version: Optional[str] = None,
    days: int = 7
):
    """Get forward evaluation metrics."""
    try:
        sys = get_sys_components()
        metrics = sys.forward_engine.get_forward_metrics(model_version, days)

        return {
            "success": True,
            "data": metrics.to_dict()
        }

    except Exception as e:
        logger.error(f"Error fetching forward metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")


@router.get("/capital/status")
async def get_capital_status():
    """Get capital controller status."""
    try:
        sys = get_sys_components()
        status = sys.capital_controller.get_status()

        return {
            "success": True,
            "data": status
        }

    except Exception as e:
        logger.error(f"Error fetching capital status: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch status")


@router.get("/capital/history")
async def get_capital_history(limit: int = 100):
    """Get capital equity history."""
    try:
        sys = get_sys_components()
        history = sys.capital_controller.get_history(limit)
        return {
            "success": True,
            "data": history
        }
    except Exception as e:
        logger.error(f"Error fetching capital history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")