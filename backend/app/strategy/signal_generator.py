"""
Signal Generator

Strategy layer that sits ABOVE models.
Models are NEVER allowed to output buy/sell directly.

Signal flow:
1. Model predictions (probabilities, expected return)
2. Regime filter
3. Confidence threshold
4. Volatility filter
5. Capital controller approval
6. Final signal

All thresholds are configurable via environment variables.
Logs every filter decision BEFORE filtering.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from app.models.xgboost_model import ModelPrediction
from app.core.regime_detector import RegimeState, MarketRegime
from app.capital.controller import CapitalController
from app.config import get_settings

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal types."""
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"
    SYSTEM_NOT_READY = "system_not_ready"


@dataclass
class TradingSignal:
    """Final trading signal with all context."""
    signal_type: SignalType
    symbol: str
    confidence: float

    # Model outputs
    prob_up: float
    prob_down: float
    expected_return: float

    # Context
    regime: MarketRegime
    volatility_score: float

    # Trade levels
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size: Optional[float] = None

    # Metadata
    model_version: str = ""
    generated_at: datetime = None
    filters_passed: Dict = None

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.utcnow()
        if self.filters_passed is None:
            self.filters_passed = {}

    def to_dict(self) -> Dict:
        return {
            "signal": self.signal_type.value,
            "symbol": self.symbol,
            "confidence": round(self.confidence, 4),
            "predictions": {
                "prob_up": round(self.prob_up, 4),
                "prob_down": round(self.prob_down, 4),
                "expected_return": round(self.expected_return, 6)
            },
            "context": {
                "regime": self.regime.value,
                "volatility_score": round(self.volatility_score, 4)
            },
            "trade": {
                "entry_price": self.entry_price,
                "stop_loss": self.stop_loss,
                "take_profit": self.take_profit,
                "position_size": self.position_size
            },
            "metadata": {
                "model_version": self.model_version,
                "generated_at": self.generated_at.isoformat(),
                "filters_passed": self.filters_passed
            }
        }


class SignalGenerator:
    """
    Main signal generation engine.

    This is the ONLY component that outputs trading signals.
    All signals pass through multiple filters before approval.
    Thresholds loaded from Settings.signal (env-var configurable).
    """

    def __init__(self, capital_controller: CapitalController):
        self.capital_controller = capital_controller

        settings = get_settings()
        self.confidence_threshold = settings.signal.confidence_threshold
        self.min_expected_return = settings.signal.min_expected_return
        self.max_volatility_score = settings.signal.max_volatility_score

        logger.info(
            f"SignalGenerator initialized: "
            f"confidence≥{self.confidence_threshold}, "
            f"return≥{self.min_expected_return}, "
            f"volatility≤{self.max_volatility_score}"
        )

    def generate(
        self,
        symbol: str,
        prediction: ModelPrediction,
        regime_state: RegimeState,
        current_price: float,
        model_version: str = ""
    ) -> TradingSignal:
        """
        Generate trading signal from model prediction.

        Signal passes through filters in order:
        1. Regime filter
        2. Confidence filter
        3. Volatility filter
        4. Expected return filter
        5. Capital controller
        """
        filters_passed = {}

        # Determine raw direction from prediction
        if prediction.prob_up > prediction.prob_down:
            raw_direction = SignalType.LONG
            raw_confidence = prediction.prob_up
        elif prediction.prob_down > prediction.prob_up:
            raw_direction = SignalType.SHORT
            raw_confidence = prediction.prob_down
        else:
            raw_direction = SignalType.NO_TRADE
            raw_confidence = 0.5

        # ── LOG PREDICTION VALUES BEFORE FILTERING ────────────────────
        logger.info(
            f"SIGNAL [{symbol}] raw prediction: "
            f"direction={raw_direction.value}, "
            f"prob_up={prediction.prob_up:.4f}, "
            f"prob_down={prediction.prob_down:.4f}, "
            f"expected_return={prediction.expected_return:.6f}, "
            f"confidence={raw_confidence:.4f}, "
            f"volatility={prediction.volatility_score:.4f}, "
            f"regime={regime_state.regime.value}, "
            f"model={model_version}"
        )

        # Filter 1: Regime
        filters_passed["regime"] = regime_state.regime.is_tradeable
        if not regime_state.regime.is_tradeable:
            logger.info(f"SIGNAL [{symbol}] FILTERED by regime={regime_state.regime.value}")
            return self._no_trade_signal(
                symbol, prediction, regime_state, current_price,
                model_version, filters_passed, "regime_filter"
            )

        # Filter 2: Confidence
        filters_passed["confidence"] = raw_confidence >= self.confidence_threshold
        if raw_confidence < self.confidence_threshold:
            logger.info(
                f"SIGNAL [{symbol}] FILTERED by confidence: "
                f"{raw_confidence:.4f} < {self.confidence_threshold}"
            )
            return self._no_trade_signal(
                symbol, prediction, regime_state, current_price,
                model_version, filters_passed, "confidence_filter"
            )

        # Filter 3: Volatility
        filters_passed["volatility"] = prediction.volatility_score <= self.max_volatility_score
        if prediction.volatility_score > self.max_volatility_score:
            logger.info(
                f"SIGNAL [{symbol}] FILTERED by volatility: "
                f"{prediction.volatility_score:.4f} > {self.max_volatility_score}"
            )
            return self._no_trade_signal(
                symbol, prediction, regime_state, current_price,
                model_version, filters_passed, "volatility_filter"
            )

        # Filter 4: Expected return
        filters_passed["expected_return"] = abs(prediction.expected_return) >= self.min_expected_return
        if abs(prediction.expected_return) < self.min_expected_return:
            logger.info(
                f"SIGNAL [{symbol}] FILTERED by expected_return: "
                f"|{prediction.expected_return:.6f}| < {self.min_expected_return}"
            )
            return self._no_trade_signal(
                symbol, prediction, regime_state, current_price,
                model_version, filters_passed, "expected_return_filter"
            )

        # Filter 5: Capital controller
        approval = self.capital_controller.can_trade(
            symbol=symbol,
            direction=raw_direction.value,
            confidence=raw_confidence,
            proposed_size=1.0,  # Placeholder, real size calculated later
            regime_tradeable=regime_state.regime.is_tradeable
        )

        filters_passed["capital"] = approval.approved
        if not approval.approved:
            logger.info(
                f"SIGNAL [{symbol}] FILTERED by capital controller: {approval.reason.value}"
            )
            return self._no_trade_signal(
                symbol, prediction, regime_state, current_price,
                model_version, filters_passed, f"capital_{approval.reason.value}"
            )

        # All filters passed — generate full signal
        logger.info(
            f"SIGNAL [{symbol}] APPROVED: {raw_direction.value} "
            f"confidence={raw_confidence:.4f}"
        )
        return TradingSignal(
            signal_type=raw_direction,
            symbol=symbol,
            confidence=raw_confidence,
            prob_up=prediction.prob_up,
            prob_down=prediction.prob_down,
            expected_return=prediction.expected_return,
            regime=regime_state.regime,
            volatility_score=prediction.volatility_score,
            entry_price=current_price,
            model_version=model_version,
            filters_passed=filters_passed
        )

    def _no_trade_signal(
        self,
        symbol: str,
        prediction: ModelPrediction,
        regime_state: RegimeState,
        current_price: float,
        model_version: str,
        filters_passed: Dict,
        rejection_reason: str
    ) -> TradingSignal:
        """Create a NO_TRADE signal."""
        filters_passed["rejection_reason"] = rejection_reason

        return TradingSignal(
            signal_type=SignalType.NO_TRADE,
            symbol=symbol,
            confidence=prediction.confidence,
            prob_up=prediction.prob_up,
            prob_down=prediction.prob_down,
            expected_return=prediction.expected_return,
            regime=regime_state.regime,
            volatility_score=prediction.volatility_score,
            entry_price=current_price,
            model_version=model_version,
            filters_passed=filters_passed
        )

    def explain_signal(self, signal: TradingSignal) -> str:
        """Generate human-readable explanation of signal."""
        if signal.signal_type == SignalType.NO_TRADE:
            reason = signal.filters_passed.get("rejection_reason", "unknown")
            return f"NO TRADE for {signal.symbol}: {reason}"

        if signal.signal_type == SignalType.SYSTEM_NOT_READY:
            return f"SYSTEM NOT READY for {signal.symbol}: no champion model"

        return (
            f"{signal.signal_type.value.upper()} {signal.symbol} "
            f"@ {signal.entry_price:.2f} "
            f"(confidence: {signal.confidence:.1%}, "
            f"regime: {signal.regime.value})"
        )
