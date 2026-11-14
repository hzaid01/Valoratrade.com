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

        n = len(df)
        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))
        logger.info(f"Data split: train={train_end}, val={val_end - train_end}, test={n - val_end}")

        # Features: computed ONCE on the full series. Every feature is causal
        # (rolling/ewm/shift(+k) only), so row t uses data <= t. Recomputing
        # per split (old code) restarted the 20/50-bar windows and threw away
        # most of the validation slice (12 samples on a 600-candle run).
        features = self.feature_engine.compute_features(df, symbol)
        feat_df = features.features[features.feature_names]

        # Labels: also computed on the full series, so every label sees its
        # real future instead of a truncated split (old code labelled the
        # last rows of each split 0 because the future was cut off).
        labels = self.target_engineer.generate_targets(df).labels
        vol = df['close'].pct_change().rolling(20).std()

        horizon = self._label_horizon()
        # Purge: drop the last `horizon` training rows, whose labels would
        # look into the validation period.
        train_rows = df.index[:max(0, train_end - horizon)]
        val_rows = df.index[train_end:val_end]

        train_X, train_y, _ = self._xy(feat_df, labels, vol, train_rows)
        val_X, val_y, val_idx = self._xy(feat_df, labels, vol, val_rows)

        if len(train_X) == 0:
            raise ValueError(f"No training samples for {symbol} after feature warm-up and purge")

        # Step 6: Train XGBoost
        xgboost_model = XGBoostDecisionModel()
        training_metrics = xgboost_model.fit(
            train_X,
            train_y['up'],
            train_y['down'],
            train_y['return'],
            train_y['volatility'],
            feature_names=features.feature_names
        )
        # These are IN-SAMPLE numbers (the model scored on its own training
        # data). Kept for compatibility, flagged so nobody reads them as skill.
        training_metrics['in_sample'] = True

        # Step 7: Out-of-sample validation = backtest of the model itself
        val_metrics = self._validate(xgboost_model, val_X, val_y, df, val_idx)

        # Save model
        patch_tst_path = ""  # PatchTST removed — XGBoost only
        xgboost_path = f"runs/{model_version}/xgboost_models.joblib"

        xgboost_model.save(xgboost_path)

        logger.info(f"Training complete for {symbol}. Validation: {val_metrics}")

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
                "val_ratio": val_ratio,
                "purge_rows": horizon,
                # Positional window the validation metrics cover; baselines
                # must be scored on the same window to be comparable.
                "val_window": [train_end, val_end],
            }
        )

    def _label_horizon(self) -> int:
        """Furthest bar any training label looks ahead."""
        try:
            hold = int(self.settings.target.max_holding_periods)
        except Exception:
            hold = 24
        return max(4, hold)

    @staticmethod
    def _xy(feat_df: pd.DataFrame, labels: pd.DataFrame, vol: pd.Series, rows: pd.Index):
        """
        Features and targets for ``rows``, aligned BY INDEX (the old code
        truncated two independently filtered arrays to a common length,
        which misaligns X and y if rows are dropped anywhere but the end).
        """
        X = feat_df.loc[feat_df.index.intersection(rows)].dropna()
        idx = X.index.intersection(labels.index)
        X = X.loc[idx]
        lab = labels.loc[idx]

        def col(name):
            return lab[name].values if name in lab.columns else np.zeros(len(lab))

        y = {
            'up': col('prob_up_4'),
            'down': col('prob_down_4'),
            'return': col('forward_return'),
            'volatility': vol.reindex(idx).fillna(0).values,
        }
        return X.values, y, idx

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

    FEE_RATE = 0.001          # same fee as BaselineStrategies
    FORWARD_HOURS = 24        # same holding period as the forward engine
    FORWARD_THRESHOLD = 0.005 # same neutral band as the forward engine

    def _validate(
        self,
        model: XGBoostDecisionModel,
        X: np.ndarray,
        y: Dict[str, np.ndarray],
        df: Optional[pd.DataFrame] = None,
        idx: Optional[pd.Index] = None
    ) -> Dict:
        """
        Out-of-sample validation.

        sharpe_ratio / max_drawdown / total_return: hourly backtest of the
        MODEL's own positions (long if prob_up > prob_down and > 0.5, short
        if the reverse, else flat), held one bar, fees on position changes,
        annualised like the baselines (sqrt(24*365)). The old code reported
        the Sharpe of the raw market return over the window (i.e. buy and
        hold) annualised with sqrt(252), so the promotion gate compared
        buy-and-hold with buy-and-hold.

        accuracy: same rule as ForwardEngine (24h return, 0.5% neutral band)
        so backtest and forward accuracy are comparable.
        """
        empty = {
            'val_accuracy_up': 0.0, 'val_accuracy_down': 0.0, 'val_samples': 0,
            'val_sharpe_ratio': 0.0, 'sharpe_ratio': 0.0, 'accuracy': 0.0,
            'max_drawdown': 0.0, 'total_return': 0.0, 'buy_hold_sharpe': 0.0,
        }
        if len(X) == 0:
            return empty

        predictions = model.predict(X)
        prob_up = np.array([p.prob_up for p in predictions])
        prob_down = np.array([p.prob_down for p in predictions])
        pred_up = prob_up > 0.5
        pred_down = prob_down > 0.5

        metrics = dict(empty)
        metrics['val_samples'] = len(X)
        metrics['val_accuracy_up'] = float((pred_up == y['up']).mean())
        metrics['val_accuracy_down'] = float((pred_down == y['down']).mean())

        if df is None or idx is None:
            return metrics

        from app.evaluation.metrics import PerformanceMetrics
        perf = PerformanceMetrics()
        close = df['close']
        pos_in_df = df.index.get_indexer(idx)

        position = np.where(pred_up & (prob_up > prob_down), 1.0,
                            np.where(pred_down & (prob_down > prob_up), -1.0, 0.0))

        # Next-bar return for each validation row (uses the bar after t,
        # which exists for every validation row except possibly the last).
        nxt = np.clip(pos_in_df + 1, 0, len(close) - 1)
        bar_ret = close.values[nxt] / close.values[pos_in_df] - 1
        bar_ret[pos_in_df + 1 >= len(close)] = 0.0
        changes = np.abs(np.diff(np.concatenate([[0.0], position])))
        strat_ret = position * bar_ret - changes * self.FEE_RATE
        equity = pd.Series(np.concatenate([[1.0], np.cumprod(1 + strat_ret)]))
        hold_equity = pd.Series(np.concatenate([[1.0], np.cumprod(1 + bar_ret)]))

        metrics['sharpe_ratio'] = perf.sharpe_ratio(equity)
        metrics['val_sharpe_ratio'] = metrics['sharpe_ratio']
        metrics['max_drawdown'] = perf.max_drawdown(equity)
        metrics['total_return'] = float(equity.iloc[-1] - 1)
        metrics['buy_hold_sharpe'] = perf.sharpe_ratio(hold_equity)

        # Directional accuracy, ForwardEngine rule
        fwd = np.clip(pos_in_df + self.FORWARD_HOURS, 0, len(close) - 1)
        has_future = pos_in_df + self.FORWARD_HOURS < len(close)
        fwd_ret = close.values[fwd] / close.values[pos_in_df] - 1
        correct = np.where(position > 0, fwd_ret > 0,
                           np.where(position < 0, fwd_ret < 0,
                                    np.abs(fwd_ret) < self.FORWARD_THRESHOLD))
        if has_future.any():
            metrics['accuracy'] = float(correct[has_future].mean())
        return metrics
