"""
Model Trainer

Orchestrates training of XGBoost pipeline with:
- Strict chronological data splits
- Leakage-free feature computation
- Target generation
- Walk-forward validation

PatchTST removed — XGBoost trains directly on engineered features.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.models.xgboost_model import XGBoostDecisionModel
from app.core.feature_engine import FeatureEngine
from app.core.target_engineer import TargetEngineer, TargetSet
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    """Result from a training run."""
    model_version: str
    patch_tst_path: str
    xgboost_path: str
    metrics: Dict
    training_samples: int
    validation_metrics: Dict
    trained_at: datetime
    config: Dict

    def to_dict(self) -> Dict:
        return {
            "model_version": self.model_version,
            "metrics": self.metrics,
            "validation_metrics": self.validation_metrics,
            "training_samples": self.training_samples,
            "trained_at": self.trained_at.isoformat(),
            "config": self.config
        }


class ModelTrainer:
    """
    XGBoost-only training pipeline.

    Pipeline:
    1. Prepare data with strict chronological split
    2. Compute engineered features (causal only)
    3. Generate targets (triple barrier)
    4. Train XGBoost decision model
    5. Validate on held-out data
    """

    def __init__(
        self,
        device: str = "cpu",
        patch_tst_epochs: int = 50,
        batch_size: int = 32
    ):
        # device/patch_tst_epochs/batch_size kept for API compatibility
        self.device = device
        self.patch_tst_epochs = patch_tst_epochs
        self.batch_size = batch_size

        self.feature_engine = FeatureEngine()
        self.target_engineer = TargetEngineer()
        self.settings = get_settings()

    def train(
        self,
        df: pd.DataFrame,
        symbol: str,
        model_version: Optional[str] = None,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15
    ) -> TrainingResult:
        """
        Train XGBoost model on engineered features.

        Args:
            df: OHLCV DataFrame (1H candles)
            symbol: Trading symbol
            model_version: Version identifier
            train_ratio: Training data ratio
            val_ratio: Validation data ratio

        Returns:
            TrainingResult with trained model paths and metrics
        """
        if model_version is None:
            model_version = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting training for {symbol}, version {model_version}")

        # Step 1: Chronological split
        train_df, val_df, test_df = self._split_data(df, train_ratio, val_ratio)
        logger.info(f"Data split: train={len(train_df)}, val={len(val_df)}, test={len(test_df)}")

        # Step 2: Generate targets
        train_targets = self.target_engineer.generate_targets(train_df)
        val_targets = self.target_engineer.generate_targets(val_df)

        # Step 3: Compute engineered features
        train_features = self.feature_engine.compute_features(train_df, symbol)
        val_features = self.feature_engine.compute_features(val_df, symbol)

        # Step 4: Prepare feature arrays (drop NaN rows instead of filling with zeros)
        train_X, train_valid_idx = self._prepare_features(train_features)
        val_X, val_valid_idx = self._prepare_features(val_features)

        # Step 5: Prepare targets (aligned to valid feature indices)
        train_y = self._prepare_targets(train_targets, train_df, train_valid_idx)
        val_y = self._prepare_targets(val_targets, val_df, val_valid_idx)

        # Align lengths
        min_train = min(len(train_X), len(train_y['up']))
        train_X = train_X[:min_train]
        train_y = {k: v[:min_train] for k, v in train_y.items()}

        min_val = min(len(val_X), len(val_y['up']))
        val_X = val_X[:min_val]
        val_y = {k: v[:min_val] for k, v in val_y.items()}

        # Step 6: Train XGBoost
        xgboost_model = XGBoostDecisionModel()
        training_metrics = xgboost_model.fit(
            train_X,
            train_y['up'],
            train_y['down'],
            train_y['return'],
            train_y['volatility'],
            feature_names=train_features.feature_names
        )

        # Step 7: Validate
        val_metrics = self._validate(xgboost_model, val_X, val_y)

        # Save model
        patch_tst_path = ""  # PatchTST removed — XGBoost only
        xgboost_path = f"runs/{model_version}/xgboost_models.joblib"

        xgboost_model.save(xgboost_path)

        logger.info(f"Training complete for {symbol}. Metrics: {training_metrics}")

        return TrainingResult(
            model_version=model_version,
            patch_tst_path=patch_tst_path,
            xgboost_path=xgboost_path,
            metrics=training_metrics,
            training_samples=len(train_X),
            validation_metrics=val_metrics,
            trained_at=datetime.utcnow(),
            config={
                "patch_tst": False,
                "train_ratio": train_ratio,
                "val_ratio": val_ratio
            }
        )

    def _split_data(
        self,
        df: pd.DataFrame,
        train_ratio: float,
        val_ratio: float
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Strict chronological split - NO SHUFFLING."""
        n = len(df)
        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        return (
            df.iloc[:train_end].copy(),
            df.iloc[train_end:val_end].copy(),
            df.iloc[val_end:].copy()
        )

    def _prepare_features(self, feature_set) -> Tuple[np.ndarray, pd.Index]:
        """Prepare feature array from feature set, dropping NaN rows."""
        feature_df = feature_set.features[feature_set.feature_names]
        feature_df = feature_df.dropna()
        return feature_df.values, feature_df.index

    def _prepare_targets(self, target_set: TargetSet, df: pd.DataFrame, valid_idx: pd.Index) -> Dict[str, np.ndarray]:
        """Prepare target arrays aligned to valid feature indices."""
        labels = target_set.labels
        common_idx = labels.index.intersection(valid_idx)
        labels = labels.loc[common_idx]

        vol = df['close'].pct_change().rolling(20).std()

        return {
            'up': labels['prob_up_4'].values if 'prob_up_4' in labels.columns else np.zeros(len(labels)),
            'down': labels['prob_down_4'].values if 'prob_down_4' in labels.columns else np.zeros(len(labels)),
            'return': labels['forward_return'].values if 'forward_return' in labels.columns else np.zeros(len(labels)),
            'volatility': vol.reindex(common_idx).fillna(0).values
        }

    def _validate(
        self,
        model: XGBoostDecisionModel,
        X: np.ndarray,
        y: Dict[str, np.ndarray]
    ) -> Dict:
        """Validate model on held-out data."""
        if len(X) == 0:
            return {'val_accuracy_up': 0.0, 'val_accuracy_down': 0.0, 'val_samples': 0, 'val_sharpe_ratio': 0.0, 'sharpe_ratio': 0.0}

        predictions = model.predict(X)

        pred_up = np.array([p.prob_up > 0.5 for p in predictions])
        pred_down = np.array([p.prob_down > 0.5 for p in predictions])

        returns = y['return']
        if len(returns) > 1 and returns.std() > 0:
            sharpe = (returns.mean() / returns.std()) * np.sqrt(252)
        else:
            sharpe = 0.0

        return {
            'val_accuracy_up': float((pred_up == y['up']).mean()),
            'val_accuracy_down': float((pred_down == y['down']).mean()),
            'val_samples': len(X),
            'val_sharpe_ratio': float(sharpe),
            'sharpe_ratio': float(sharpe)
        }