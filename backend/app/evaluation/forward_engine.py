"""
Forward-Only Evaluation Engine

Separate from backtesting - this engine:
- Stores locked predictions at prediction time
- Resolves outcomes only after time passes
- Never recomputes historical predictions
- Feeds the retraining pipeline with forward truth
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional
from uuid import uuid4
import pandas as pd
from google.cloud.firestore import Query

from app.firebase_config import get_firestore
from app.core.fs_tx import run_transaction

logger = logging.getLogger(__name__)

class PredictionStatus(str, Enum):
    """
    Prediction lifecycle (Firestore field ``status``; single source of truth).

        PENDING --resolve--> RESOLVED   (exit price at maturity known)
        PENDING --expire---> EXPIRED    (no exit price EXPIRE_AFTER past maturity)

    RESOLVED and EXPIRED are terminal; resolving them again is a no-op.
    ``is_resolved`` is still written as a read-only mirror meaning "no longer
    pending" because the existing Firestore index and queries use it.
    """
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


PREDICTION_TRANSITIONS = {
    PredictionStatus.PENDING: {PredictionStatus.RESOLVED, PredictionStatus.EXPIRED},
    PredictionStatus.RESOLVED: set(),
    PredictionStatus.EXPIRED: set(),
}

EXPIRE_AFTER = timedelta(days=7)


def _naive_utc(ts):
    """Firestore returns tz-aware datetimes; the engine uses naive UTC."""
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def prediction_status(d: Dict) -> PredictionStatus:
    """Status of a stored prediction doc (legacy docs: from is_resolved)."""
    if d.get("status"):
        try:
            return PredictionStatus(d["status"])
        except ValueError:
            logger.error(f"Unknown prediction status {d['status']!r} on {d.get('id')}")
    return PredictionStatus.RESOLVED if d.get("is_resolved") else PredictionStatus.PENDING


@dataclass
class PredictionRecord:
    id: str
    timestamp: datetime
    symbol: str
    direction: str
    confidence: float
    entry_price: float
    model_version: str
    model_id: str
    # Fields set after resolution
    is_resolved: bool = False
    outcome: Optional[str] = None
    exit_price: Optional[float] = None
    actual_return: Optional[float] = None
    resolved_at: Optional[datetime] = None
    status: str = PredictionStatus.PENDING.value
    position_size: Optional[float] = None
    shadow: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, 'isoformat') else self.timestamp,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "model_version": self.model_version,
            "model_id": self.model_id,
            "is_resolved": self.is_resolved,
            "outcome": self.outcome,
            "exit_price": self.exit_price,
            "actual_return": self.actual_return,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at and hasattr(self.resolved_at, 'isoformat') else self.resolved_at,
            "status": self.status,
            "position_size": self.position_size,
            "shadow": self.shadow,
        }

@dataclass
class ForwardMetrics:
    total_predictions: int
    resolved_predictions: int
    pending_predictions: int
    accuracy: float
    avg_confidence: float
    avg_return_when_followed: float
    profitable_rate: float
    model_version: str
    period_start: datetime
    period_end: datetime

    def to_dict(self):
        return {
            "total_predictions": self.total_predictions,
            "resolved_predictions": self.resolved_predictions,
            "pending_predictions": self.pending_predictions,
            "accuracy": self.accuracy,
            "avg_confidence": self.avg_confidence,
            "avg_return_when_followed": self.avg_return_when_followed,
            "profitable_rate": self.profitable_rate,
            "model_version": self.model_version,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat()
        }

class ForwardEngine:
    """
    Forward-only evaluation engine with Firestore persistence.
    """
    
    def __init__(
        self,
        holding_period_hours: int = 24,
        profit_threshold: float = 0.005
    ):
        self.holding_period = timedelta(hours=holding_period_hours)
        self.profit_threshold = profit_threshold
        self.db = get_firestore()
        self.collection = self.db.collection('predictions')
    
    def log_prediction(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        entry_price: float,
        model_version: str,
        model_id: str = "",
        position_size: Optional[float] = None,
        shadow: bool = False
    ) -> PredictionRecord:
        """
        Log a new prediction (LOCKED at this moment, status PENDING).
        
        Write-once enforcement: the prediction fields are never modified;
        resolution only adds outcome fields.
        """
        record_id = str(uuid4())
        timestamp = datetime.utcnow()
        
        record_data = {
            "id": record_id,
            "timestamp": timestamp,
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "entry_price": entry_price,
            "model_version": model_version,
            "model_id": model_id or model_version,
            "status": PredictionStatus.PENDING.value,
            "is_resolved": False,
            "outcome": None,
            "position_size": position_size,
            "shadow": shadow,
            "created_at": timestamp 
        }
        
        self.collection.document(record_id).set(record_data)
        logger.info(f"Logged prediction: {record_id} {symbol} {direction} @ {entry_price}")
        return self._dict_to_record(record_data)

    def _outcome(self, direction: str, entry_price: float, exit_price: float):
        if direction == 'long':
            actual_return = (exit_price - entry_price) / entry_price
        elif direction == 'short':
            actual_return = (entry_price - exit_price) / entry_price
        else:  # hold / no_trade
            actual_return = 0.0
        if actual_return > self.profit_threshold:
            outcome = 'win'
        elif actual_return < -self.profit_threshold:
            outcome = 'loss'
        else:
            outcome = 'neutral'
        return actual_return, outcome

    def _finish(self, pred_id: str, target: PredictionStatus, fields: Dict) -> Optional[Dict]:
        """
        Atomically move one prediction PENDING -> target. Returns the updated
        doc if THIS call made the transition, None if it was already terminal
        (idempotent: another instance or an earlier run got there first).
        """
        ref = self.collection.document(pred_id)

        def _tx(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return None
            d = snap.to_dict()
            current = prediction_status(d)
            if target not in PREDICTION_TRANSITIONS[current]:
                return None
            update = dict(fields)
            update["status"] = target.value
            update["is_resolved"] = True  # mirror: no longer pending
            transaction.update(ref, update)
            d.update(update)
            return d

        return run_transaction(self.db, _tx)

    def resolve_pending(
        self,
        price_at: Callable[[str, datetime], Optional[float]],
        now: Optional[datetime] = None
    ) -> List[PredictionRecord]:
        """
        Resolve every PENDING prediction whose holding period has passed,
        using ``price_at(symbol, maturity_time)`` as the exit price. If no
        price is available EXPIRE_AFTER past maturity, the prediction is
        EXPIRED (excluded from metrics) instead of staying pending forever.

        Returns only predictions resolved by THIS call, so callers can apply
        side effects (equity) exactly once per prediction.
        """
        now = now or datetime.utcnow()
        resolved: List[PredictionRecord] = []
        for doc in self.collection.where("is_resolved", "==", False).stream():
            d = doc.to_dict()
            if prediction_status(d) != PredictionStatus.PENDING:
                continue
            ts = _naive_utc(d.get("timestamp"))
            if ts is None:
                continue
            maturity = ts + self.holding_period
            if now < maturity:
                continue

            exit_price = None
            try:
                exit_price = price_at(d["symbol"], maturity)
            except Exception as e:
                logger.warning(f"No exit price for {d.get('id')} {d.get('symbol')}: {e}")

            if exit_price is None or exit_price <= 0:
                if now - maturity > EXPIRE_AFTER:
                    if self._finish(d["id"], PredictionStatus.EXPIRED, {"expired_at": now}):
                        logger.warning(f"Expired prediction {d['id']}: no exit price since {maturity}")
                continue

            actual_return, outcome = self._outcome(d["direction"], d["entry_price"], exit_price)
            done = self._finish(d["id"], PredictionStatus.RESOLVED, {
                "exit_price": exit_price,
                "actual_return": actual_return,
                "outcome": outcome,
                "resolved_at": now,
            })
            if done:
                rec = self._dict_to_record(done)
                resolved.append(rec)
                logger.info(f"Resolved: {rec.id} {rec.symbol} {outcome} {actual_return:.4f}")
        return resolved

    def resolve_outcomes(
        self,
        current_prices: Dict[str, float]
    ) -> List[PredictionRecord]:
        """
        Resolve matured predictions with the given CURRENT prices (kept for
        compatibility). Prefer resolve_pending() with a price-at-maturity
        source; with current prices, a late run uses a late exit price.
        """
        return self.resolve_pending(lambda symbol, _t: current_prices.get(symbol))

    def _load(self, model_version: Optional[str], cutoff: datetime) -> List[PredictionRecord]:
        if model_version:
            # Single-field equality (no composite index needed); time filtered here.
            docs = self.collection.where("model_version", "==", model_version).stream()
        else:
            docs = self.collection.where("timestamp", ">=", cutoff).stream()
        records = []
        for doc in docs:
            d = doc.to_dict()
            if not model_version and d.get("shadow"):
                continue  # "all" means served predictions
            rec = self._dict_to_record(d)
            if rec.timestamp is not None and rec.timestamp >= cutoff:
                records.append(rec)
        return records

    def get_forward_metrics(
        self,
        model_version: Optional[str] = None,
        days: int = 7
    ) -> ForwardMetrics:
        """
        Calculate forward metrics for a model from Firestore (previously
        from this process's in-memory predictions only).
        
        Only uses RESOLVED predictions; EXPIRED ones are excluded.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        recent = [
            p for p in self._load(model_version, cutoff)
            if p.status != PredictionStatus.EXPIRED.value
        ]
        resolved = [p for p in recent if p.status == PredictionStatus.RESOLVED.value]
        
        if not resolved:
            return ForwardMetrics(
                total_predictions=len(recent),
                resolved_predictions=0,
                pending_predictions=len(recent),
                accuracy=0.0,
                avg_confidence=0.0,
                avg_return_when_followed=0.0,
                profitable_rate=0.0,
                model_version=model_version or "all",
                period_start=cutoff,
                period_end=datetime.utcnow()
            )
        
        total = len(recent)
        resolved_count = len(resolved)
        pending = total - resolved_count
        
        # Accuracy: did direction match outcome? (no_trade is scored like hold)
        correct = sum(
            1 for p in resolved
            if (p.direction == 'long' and p.actual_return > 0) or
               (p.direction == 'short' and p.actual_return < 0) or
               (p.direction in ('hold', 'no_trade') and abs(p.actual_return) < self.profit_threshold)
        )
        accuracy = correct / resolved_count if resolved_count > 0 else 0
        
        avg_confidence = sum(p.confidence for p in resolved) / resolved_count
        avg_return = sum(p.actual_return for p in resolved) / resolved_count
        
        profitable = sum(1 for p in resolved if p.outcome == 'win')
        profitable_rate = profitable / resolved_count
        
        return ForwardMetrics(
            total_predictions=total,
            resolved_predictions=resolved_count,
            pending_predictions=pending,
            accuracy=accuracy,
            avg_confidence=avg_confidence,
            avg_return_when_followed=avg_return,
            profitable_rate=profitable_rate,
            model_version=model_version or "all",
            period_start=cutoff,
            period_end=datetime.utcnow()
        )
    
    def get_pending_predictions(self) -> List[PredictionRecord]:
        """Get all unresolved predictions."""
        docs = self.collection.where("is_resolved", "==", False).stream()
        records = []
        for doc in docs:
            d = doc.to_dict()
            records.append(self._dict_to_record(d))
        return records

    def get_prediction_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get history of predictions for a symbol."""
        query = (
            self.collection
            .where("symbol", "==", symbol)
            .order_by("timestamp", direction=Query.DESCENDING)
            .limit(limit)
        )
        docs = query.stream()
        
        history = []
        for doc in docs:
            d = doc.to_dict()
            if d.get("shadow"):
                continue  # challenger shadow predictions are internal only
            # Convert timestamp to ISO string for API response
            if isinstance(d.get('timestamp'), datetime):
                d['timestamp'] = d['timestamp'].isoformat()
            if isinstance(d.get('resolved_at'), datetime):
                d['resolved_at'] = d['resolved_at'].isoformat()
            history.append(d)
            
        return history

    def _dict_to_record(self, d: Dict) -> PredictionRecord:
        """Helper to convert a stored dict to PredictionRecord."""
        status = prediction_status(d)
        return PredictionRecord(
            id=d['id'],
            timestamp=_naive_utc(d.get('timestamp')),
            symbol=d['symbol'],
            direction=d['direction'],
            confidence=d['confidence'],
            entry_price=d['entry_price'],
            model_version=d['model_version'],
            model_id=d.get('model_id', ''),
            is_resolved=status != PredictionStatus.PENDING,
            outcome=d.get('outcome'),
            exit_price=d.get('exit_price'),
            actual_return=d.get('actual_return'),
            resolved_at=_naive_utc(d.get('resolved_at')),
            status=status.value,
            position_size=d.get('position_size'),
            shadow=bool(d.get('shadow', False)),
        )
    
    def get_resolved_for_retraining(
        self,
        model_version: str,
        min_date: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Get resolved predictions as training data.
        
        This feeds the retraining pipeline.
        """
        preds = [
            self._dict_to_record(doc.to_dict())
            for doc in self.collection.where("model_version", "==", model_version).stream()
        ]
        preds = [p for p in preds if p.status == PredictionStatus.RESOLVED.value]
        
        if min_date:
            preds = [p for p in preds if p.timestamp >= min_date]
        
        if not preds:
            return pd.DataFrame()
        
        data = [p.to_dict() for p in preds]
        return pd.DataFrame(data)
    
    def compare_models(
        self,
        model_versions: List[str],
        days: int = 7
    ) -> pd.DataFrame:
        """Compare forward performance of multiple models."""
        results = []
        
        for version in model_versions:
            metrics = self.get_forward_metrics(version, days)
            results.append({
                "model": version,
                "accuracy": metrics.accuracy,
                "profitable_rate": metrics.profitable_rate,
                "avg_return": metrics.avg_return_when_followed,
                "predictions": metrics.resolved_predictions
            })
        
        return pd.DataFrame(results).sort_values('profitable_rate', ascending=False)
