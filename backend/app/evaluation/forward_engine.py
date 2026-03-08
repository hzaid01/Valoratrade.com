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
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4
import pandas as pd
from google.cloud.firestore import Query

from app.firebase_config import get_firestore

logger = logging.getLogger(__name__)

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
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at and hasattr(self.resolved_at, 'isoformat') else self.resolved_at
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
        # In-memory cache for predictions (write-once enforced)
        self._predictions: Dict[str, PredictionRecord] = {}
        self._by_model: Dict[str, List[str]] = {}
    
    def log_prediction(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        entry_price: float,
        model_version: str,
        model_id: str = ""
    ) -> PredictionRecord:
        """
        Log a new prediction (LOCKED at this moment).
        
        Write-once enforcement: once logged, prediction cannot be modified.
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
            "is_resolved": False,
            "outcome": None,
            "created_at": timestamp 
        }
        
        # Write to Firestore (immutable after this point)
        self.collection.document(record_id).set(record_data)
        
        # Create record object
        record = PredictionRecord(
            id=record_id,
            timestamp=timestamp,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            entry_price=entry_price,
            model_version=model_version,
            model_id=model_id or model_version
        )
        
        # Update in-memory caches
        self._predictions[record_id] = record
        
        effective_model_id = model_id or model_version
        if effective_model_id not in self._by_model:
            self._by_model[effective_model_id] = []
        self._by_model[effective_model_id].append(record_id)
        
        logger.info(f"Logged prediction: {record_id} {symbol} {direction} @ {entry_price}")
        
        return record
    
    def resolve_outcomes(
        self,
        current_prices: Dict[str, float]
    ) -> List[PredictionRecord]:
        """
        Resolve outcomes for predictions past holding period.
        
        This should be called periodically (e.g., hourly).
        Only predictions older than holding_period are resolved.
        """
        now = datetime.utcnow()
        resolved = []
        
        for pred_id, pred in self._predictions.items():
            # Skip already resolved
            if pred.is_resolved:
                continue
            
            # Check if holding period passed
            if now - pred.timestamp < self.holding_period:
                continue
            
            # Get current price for symbol
            if pred.symbol not in current_prices:
                logger.warning(f"No price for {pred.symbol}, skipping resolution")
                continue
            
            exit_price = current_prices[pred.symbol]
            
            # Calculate actual return
            if pred.direction == 'long':
                actual_return = (exit_price - pred.entry_price) / pred.entry_price
            elif pred.direction == 'short':
                actual_return = (pred.entry_price - exit_price) / pred.entry_price
            else:  # hold
                actual_return = 0.0
            
            # Determine outcome
            if actual_return > self.profit_threshold:
                outcome = 'win'
            elif actual_return < -self.profit_threshold:
                outcome = 'loss'
            else:
                outcome = 'neutral'
            
            # Update record
            pred.exit_price = exit_price
            pred.actual_return = actual_return
            pred.outcome = outcome
            pred.resolved_at = now
            
            resolved.append(pred)
            logger.info(f"Resolved: {pred.id} {pred.symbol} {outcome} {actual_return:.4f}")
        
        return resolved
    
    def get_forward_metrics(
        self,
        model_version: Optional[str] = None,
        days: int = 7
    ) -> ForwardMetrics:
        """
        Calculate forward metrics for a model.
        
        Only uses RESOLVED predictions (no pending).
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        # Filter predictions
        if model_version:
            pred_ids = self._by_model.get(model_version, [])
            preds = [self._predictions[pid] for pid in pred_ids]
        else:
            preds = list(self._predictions.values())
        
        # Filter by time and resolution status
        recent = [p for p in preds if p.timestamp >= cutoff]
        resolved = [p for p in recent if p.is_resolved]
        
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
        
        # Calculate metrics
        total = len(recent)
        resolved_count = len(resolved)
        pending = total - resolved_count
        
        # Accuracy: did direction match outcome?
        correct = sum(
            1 for p in resolved
            if (p.direction == 'long' and p.actual_return > 0) or
               (p.direction == 'short' and p.actual_return < 0) or
               (p.direction == 'hold' and abs(p.actual_return) < self.profit_threshold)
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
            # Convert timestamp to ISO string for API response
            if isinstance(d.get('timestamp'), datetime):
                d['timestamp'] = d['timestamp'].isoformat()
            if isinstance(d.get('resolved_at'), datetime):
                d['resolved_at'] = d['resolved_at'].isoformat()
            history.append(d)
            
        return history

    def _dict_to_record(self, d: Dict) -> PredictionRecord:
        """Helper to convert dict to PredictionRecord."""
        # Handle Firestore timestamps
        ts = d['timestamp']
        if hasattr(ts, 'timestamp'): # Check if datetime-like
             pass # keeping as is, but PredictionRecord expects datetime
        
        return PredictionRecord(
            id=d['id'],
            timestamp=d['timestamp'],
            symbol=d['symbol'],
            direction=d['direction'],
            confidence=d['confidence'],
            entry_price=d['entry_price'],
            model_version=d['model_version'],
            model_id=d.get('model_id', ''),
            outcome=d.get('outcome'),
            exit_price=d.get('exit_price'),
            actual_return=d.get('actual_return'),
            resolved_at=d.get('resolved_at')
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
        pred_ids = self._by_model.get(model_version, [])
        preds = [self._predictions[pid] for pid in pred_ids if self._predictions[pid].is_resolved]
        
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
