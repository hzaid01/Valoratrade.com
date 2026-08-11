"""
Walk-Forward Validation

Proper time-series cross-validation with:
- Expanding training window
- Fixed test window
- No data leakage
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.evaluation.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    """Single walk-forward fold result."""
    fold_number: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_samples: int
    test_samples: int
    metrics: Dict


@dataclass
class WalkForwardResult:
    """Complete walk-forward validation result."""
    n_folds: int
    folds: List[WalkForwardFold]
    aggregate_metrics: Dict
    stability_score: float
    
    def to_dict(self) -> Dict:
        return {
            "n_folds": self.n_folds,
            "aggregate_metrics": self.aggregate_metrics,
            "stability_score": self.stability_score,
            "folds": [
                {
                    "fold": f.fold_number,
                    "train_samples": f.train_samples,
                    "test_samples": f.test_samples,
                    "metrics": f.metrics
                }
                for f in self.folds
            ]
        }


class WalkForwardValidator:
    """
    Walk-forward cross-validation for time series.
    
    Unlike standard k-fold, this respects temporal ordering:
    - Training data is always BEFORE test data
    - Training window can be expanding or sliding
    - Ensures no future information leaks into training
    """
    
    def __init__(
        self,
        n_splits: int = 5,
        test_size: int = 168,  # 1 week of hourly candles
        expanding: bool = True  # Expanding vs sliding window
    ):
        self.n_splits = n_splits
        self.test_size = test_size
        self.expanding = expanding
        self.metrics_calculator = PerformanceMetrics()
    
    def split(
        self,
        n_samples: int
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate train/test indices for each fold.
        
        Args:
            n_samples: Total number of samples
            
        Returns:
            List of (train_indices, test_indices) tuples
        """
        min_train_size = n_samples // (self.n_splits + 1)
        
        splits = []
        for i in range(self.n_splits):
            if self.expanding:
                # Expanding window: train from start to split point
                train_end = min_train_size * (i + 1)
            else:
                # Sliding window: fixed train size
                train_start = min_train_size * i
                train_end = min_train_size * (i + 1)
            
            test_start = train_end
            test_end = min(test_start + self.test_size, n_samples)
            
            if test_end <= test_start:
                continue
            
            if self.expanding:
                train_indices = np.arange(0, train_end)
            else:
                train_indices = np.arange(train_start, train_end)
            
            test_indices = np.arange(test_start, test_end)
            
            splits.append((train_indices, test_indices))
        
        return splits
    
    def validate(
        self,
        df: pd.DataFrame,
        train_fn,
        predict_fn
    ) -> WalkForwardResult:
        """
        Perform walk-forward validation.
        
        Args:
            df: Full dataset
            train_fn: Function(train_df) -> model
            predict_fn: Function(model, test_df) -> predictions
            
        Returns:
            WalkForwardResult with all fold results
        """
        splits = self.split(len(df))
        folds = []
        all_metrics = []
        
        for i, (train_idx, test_idx) in enumerate(splits):
            logger.info(f"Walk-forward fold {i + 1}/{len(splits)}")
            
            train_df = df.iloc[train_idx].copy()
            test_df = df.iloc[test_idx].copy()
            
            # Train on this fold
            model = train_fn(train_df)
            
            # Predict on test
            predictions = predict_fn(model, test_df)
            
            # Calculate metrics
            fold_metrics = self._calculate_fold_metrics(test_df, predictions)
            all_metrics.append(fold_metrics)
            
            fold = WalkForwardFold(
                fold_number=i + 1,
                train_start=train_df.index[0],
                train_end=train_df.index[-1],
                test_start=test_df.index[0],
                test_end=test_df.index[-1],
                train_samples=len(train_idx),
                test_samples=len(test_idx),
                metrics=fold_metrics
            )
            folds.append(fold)
        
        # Aggregate metrics
        aggregate = self._aggregate_metrics(all_metrics)
        stability = self._calculate_stability(all_metrics)
        
        return WalkForwardResult(
            n_folds=len(folds),
            folds=folds,
            aggregate_metrics=aggregate,
            stability_score=stability
        )
    
    def _calculate_fold_metrics(
        self,
        test_df: pd.DataFrame,
        predictions: List
    ) -> Dict:
        """Calculate metrics for a single fold."""
        if not predictions:
            return {'accuracy': 0.0, 'avg_confidence': 0.0}
        
        # Direction accuracy
        actual_returns = test_df['close'].pct_change().shift(-1).dropna()
        
        correct = 0
        total_confidence = 0
        
        for i, pred in enumerate(predictions[:len(actual_returns)]):
            actual_dir = 'long' if actual_returns.iloc[i] > 0 else 'short'
            pred_dir = pred.direction if hasattr(pred, 'direction') else 'hold'
            
            if pred_dir == actual_dir:
                correct += 1
            
            if hasattr(pred, 'confidence'):
                total_confidence += pred.confidence
        
        n = len(predictions[:len(actual_returns)])
        
        return {
            'accuracy': correct / n if n > 0 else 0.0,
            'avg_confidence': total_confidence / n if n > 0 else 0.0,
            'samples': n
        }
    
    def _aggregate_metrics(self, all_metrics: List[Dict]) -> Dict:
        """Aggregate metrics across folds."""
        if not all_metrics:
            return {}
        
        keys = all_metrics[0].keys()
        aggregate = {}
        
        for key in keys:
            if key == 'samples':
                aggregate[key] = sum(m.get(key, 0) for m in all_metrics)
            else:
                values = [m.get(key, 0) for m in all_metrics]
                aggregate[f"{key}_mean"] = float(np.mean(values))
                aggregate[f"{key}_std"] = float(np.std(values))
        
        return aggregate
    
    def _calculate_stability(self, all_metrics: List[Dict]) -> float:
        """
        Calculate stability score.
        
        Higher score = more consistent performance across folds.
        """
        if not all_metrics or 'accuracy' not in all_metrics[0]:
            return 0.0
        
        accuracies = [m['accuracy'] for m in all_metrics]
        
        if np.std(accuracies) == 0:
            return 1.0
        
        # Coefficient of variation (lower is more stable)
        cv = np.std(accuracies) / (np.mean(accuracies) + 1e-10)
        
        # Convert to stability score (higher is better)
        stability = 1 / (1 + cv)
        
        return float(stability)
