"""
XGBoost Decision Model

The decision-making model that consumes:
- PatchTST embeddings
- Engineered features
- Market structure features

Outputs trading-useful predictions, NOT raw prices.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
import joblib

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── GCS bucket name ──────────────────────────────────────────────────────────
GCS_BUCKET = "valoratrade-models"


def _get_gcs_client():
    from google.cloud import storage
    return storage.Client()


def _ensure_bucket():
    """Ensure GCS bucket exists, create if not."""
    client = _get_gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    if not bucket.exists():
        logger.info(f"Creating GCS bucket {GCS_BUCKET}...")
        bucket = client.create_bucket(GCS_BUCKET, location="asia-south1")
    return bucket


def _normalize_gcs_path(path: str) -> str:
    """Convert Windows backslashes to forward slashes for GCS."""
    return path.replace("\\", "/").lstrip("/")


def _upload_to_gcs(local_path: str) -> None:
    """Upload a local file to GCS."""
    try:
        bucket = _ensure_bucket()
        gcs_path = _normalize_gcs_path(local_path)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(local_path)
        logger.info(f"Model uploaded to GCS: gs://{GCS_BUCKET}/{gcs_path}")
    except Exception as e:
        logger.error(f"Failed to upload model to GCS: {e}")
        raise


def _download_from_gcs(gcs_path: str, local_path: str) -> None:
    """Download a file from GCS to local path."""
    try:
        bucket = _ensure_bucket()
        normalized = _normalize_gcs_path(gcs_path)
        blob = bucket.blob(normalized)

        if not blob.exists():
            raise FileNotFoundError(
                f"Model not found in GCS: gs://{GCS_BUCKET}/{normalized}"
            )

        os.makedirs(
            os.path.dirname(local_path) if os.path.dirname(local_path) else ".",
            exist_ok=True,
        )
        blob.download_to_filename(local_path)
        logger.info(f"Model downloaded from GCS: gs://{GCS_BUCKET}/{normalized}")
    except Exception as e:
        logger.error(f"Failed to download model from GCS: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ModelPrediction:
    """Structured prediction output."""
    prob_up: float          # Probability of upward move
    prob_down: float        # Probability of downward move
    expected_return: float  # Expected return magnitude
    confidence: float       # Overall confidence
    volatility_score: float # Risk proxy

    @property
    def direction(self) -> str:
        """Get predicted direction."""
        if self.prob_up > self.prob_down and self.prob_up > 0.5:
            return 'long'
        elif self.prob_down > self.prob_up and self.prob_down > 0.5:
            return 'short'
        return 'hold'

    def to_dict(self) -> Dict:
        return {
            "prob_up": round(self.prob_up, 4),
            "prob_down": round(self.prob_down, 4),
            "expected_return": round(self.expected_return, 6),
            "confidence": round(self.confidence, 4),
            "volatility_score": round(self.volatility_score, 4),
            "direction": self.direction
        }


class XGBoostDecisionModel:
    """
    XGBoost-based decision model.

    This is the ONLY model that makes trading decisions.
    PatchTST embeddings are just one input among many.

    Multi-target outputs:
    - prob_up: Classification probability of profitable long
    - prob_down: Classification probability of profitable short
    - expected_return: Regression target
    - volatility_score: Risk estimation
    """

    VERSION = "1.0.0"

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 6,
        learning_rate: float = 0.05,
        random_state: int = 42
    ):
        settings = get_settings()

        self.n_estimators = n_estimators or settings.model.xgb_n_estimators
        self.max_depth = max_depth or settings.model.xgb_max_depth
        self.learning_rate = learning_rate or settings.model.xgb_learning_rate
        self.random_state = random_state

        # Separate models for each target
        self.model_up: Optional[xgb.XGBClassifier] = None
        self.model_down: Optional[xgb.XGBClassifier] = None
        self.model_return: Optional[xgb.XGBRegressor] = None
        self.model_volatility: Optional[xgb.XGBRegressor] = None

        # Feature scaler
        self.scaler = StandardScaler()
        self.feature_names: List[str] = []

        # Metadata
        self.trained_at: Optional[datetime] = None
        self.training_samples: int = 0

    # ── Training ─────────────────────────────────────────────────────────────

    def fit(
        self,
        X: np.ndarray,
        y_up: np.ndarray,
        y_down: np.ndarray,
        y_return: np.ndarray,
        y_volatility: np.ndarray,
        feature_names: Optional[List[str]] = None
    ) -> Dict:
        """
        Train all component models.

        Args:
            X: Feature matrix (n_samples, n_features)
            y_up: Binary labels for profitable long (0/1)
            y_down: Binary labels for profitable short (0/1)
            y_return: Continuous return targets
            y_volatility: Volatility targets
            feature_names: Optional feature names

        Returns:
            Training metrics
        """
        logger.info(f"Training XGBoost models on {len(X)} samples")

        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        self.feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

        # Common parameters
        params = {
            'n_estimators': self.n_estimators,
            'max_depth': self.max_depth,
            'learning_rate': self.learning_rate,
            'random_state': self.random_state,
            'n_jobs': -1,
            'verbosity': 0
        }

        # Train prob_up classifier
        self.model_up = xgb.XGBClassifier(**params, objective='binary:logistic')
        self.model_up.fit(X_scaled, y_up)

        # Train prob_down classifier
        self.model_down = xgb.XGBClassifier(**params, objective='binary:logistic')
        self.model_down.fit(X_scaled, y_down)

        # Train return regressor
        self.model_return = xgb.XGBRegressor(**params, objective='reg:squarederror')
        self.model_return.fit(X_scaled, y_return)

        # Train volatility regressor
        self.model_volatility = xgb.XGBRegressor(**params, objective='reg:squarederror')
        self.model_volatility.fit(X_scaled, y_volatility)

        self.trained_at = datetime.utcnow()
        self.training_samples = len(X)

        # Calculate training metrics
        metrics = self._calculate_training_metrics(
            X_scaled, y_up, y_down, y_return, y_volatility
        )

        logger.info(f"Training complete. Accuracy up: {metrics['accuracy_up']:.3f}")

        return metrics

    # ── Inference ────────────────────────────────────────────────────────────

    def predict(self, X: np.ndarray) -> List[ModelPrediction]:
        """
        Generate predictions for input features.

        Args:
            X: Feature matrix (n_samples, n_features)

        Returns:
            List of ModelPrediction objects
        """
        if self.model_up is None:
            raise ValueError("Model not trained. Call fit() first.")

        X_scaled = self.scaler.transform(X)

        prob_up = self.model_up.predict_proba(X_scaled)[:, 1]
        prob_down = self.model_down.predict_proba(X_scaled)[:, 1]
        expected_return = self.model_return.predict(X_scaled)
        volatility = self.model_volatility.predict(X_scaled)

        predictions = []
        for i in range(len(X)):
            confidence = max(prob_up[i], prob_down[i])
            vol_score = min(max(volatility[i], 0), 1)

            predictions.append(ModelPrediction(
                prob_up=float(prob_up[i]),
                prob_down=float(prob_down[i]),
                expected_return=float(expected_return[i]),
                confidence=float(confidence),
                volatility_score=float(vol_score)
            ))

        return predictions

    def predict_single(self, features: np.ndarray) -> ModelPrediction:
        """Predict for a single sample."""
        if features.ndim == 1:
            features = features.reshape(1, -1)
        return self.predict(features)[0]

    # ── Feature importance ───────────────────────────────────────────────────

    def get_feature_importance(self) -> Dict[str, float]:
        """Get feature importance from the up model."""
        if self.model_up is None:
            return {}
        importance = self.model_up.feature_importances_
        return dict(zip(self.feature_names, importance))

    # ── Internal metrics ─────────────────────────────────────────────────────

    def _calculate_training_metrics(
        self,
        X: np.ndarray,
        y_up: np.ndarray,
        y_down: np.ndarray,
        y_return: np.ndarray,
        y_volatility: np.ndarray
    ) -> Dict:
        """Calculate training performance metrics."""
        pred_up = self.model_up.predict(X)
        pred_down = self.model_down.predict(X)
        pred_return = self.model_return.predict(X)
        pred_vol = self.model_volatility.predict(X)

        return {
            'accuracy_up': float((pred_up == y_up).mean()),
            'accuracy_down': float((pred_down == y_down).mean()),
            'mse_return': float(np.mean((pred_return - y_return) ** 2)),
            'mse_volatility': float(np.mean((pred_vol - y_volatility) ** 2)),
            'samples': len(X)
        }

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """
        Save model to disk then upload to Google Cloud Storage.
        GCS is the source of truth for Cloud Run persistence.
        """
        os.makedirs(
            os.path.dirname(path) if os.path.dirname(path) else ".",
            exist_ok=True
        )

        data = {
            'version': self.VERSION,
            'model_up': self.model_up,
            'model_down': self.model_down,
            'model_return': self.model_return,
            'model_volatility': self.model_volatility,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'trained_at': self.trained_at,
            'training_samples': self.training_samples,
            'config': {
                'n_estimators': self.n_estimators,
                'max_depth': self.max_depth,
                'learning_rate': self.learning_rate
            }
        }

        # Save locally first
        joblib.dump(data, path)
        logger.info(f"XGBoost model saved locally: {path}")

        # Upload to GCS — this is what Cloud Run will use
        _upload_to_gcs(path)

    @classmethod
    def load(cls, path: str) -> 'XGBoostDecisionModel':
        """
        Load model from local disk.
        If not found locally, download from Google Cloud Storage.
        """
        if not os.path.exists(path):
            logger.info(f"Model not found locally at {path}, downloading from GCS...")
            _download_from_gcs(gcs_path=path, local_path=path)

        data = joblib.load(path)

        model = cls(
            n_estimators=data['config']['n_estimators'],
            max_depth=data['config']['max_depth'],
            learning_rate=data['config']['learning_rate']
        )

        model.model_up = data['model_up']
        model.model_down = data['model_down']
        model.model_return = data['model_return']
        model.model_volatility = data['model_volatility']
        model.scaler = data['scaler']
        model.feature_names = data['feature_names']
        model.trained_at = data['trained_at']
        model.training_samples = data['training_samples']

        logger.info(f"XGBoost model loaded from {path}")
        return model