"""
Helpers to run app.api.training.run_training_job end-to-end against the
in-memory Firestore with the heavy ML pieces stubbed out.
"""
import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd


def make_df(n=600):
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, n))
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": np.ones(n) * 10,
    }, index=idx)


class StubConfig:
    """Mutable knobs for one test."""
    def __init__(self):
        self.insufficient_data = False
        self.train_raises = None
        self.metrics = {"accuracy": 0.6, "sharpe_ratio": 1.5, "max_drawdown": 0.05}
        self.validation_metrics = {"accuracy": 0.58, "sharpe_ratio": 1.2, "max_drawdown": 0.05}
        self.baseline_sharpe = 0.1


def install(monkeypatch, tmp_path, cfg: StubConfig = None):
    """Patch heavy dependencies of run_training_job. Returns the config."""
    cfg = cfg or StubConfig()
    monkeypatch.chdir(tmp_path)

    import app.api.training as training
    from app.core.data_store import InsufficientDataError

    class FakeDataStore:
        MIN_CANDLES_FOR_TRAINING = 500

        def get_training_data(self, symbol, tf, n):
            if cfg.insufficient_data:
                raise InsufficientDataError(f"{symbol}: only 10 candles")
            return make_df()

        def get_data_stats(self, symbol, timeframe="1h"):
            return {"candle_count": 10 if cfg.insufficient_data else 1000}

    monkeypatch.setattr(training, "DataStore", FakeDataStore)

    import app.core.feature_engine as fe
    monkeypatch.setattr(fe, "FeatureEngine", lambda: SimpleNamespace(compute_features=lambda *a, **k: None))

    import app.models.training.trainer as trainer_mod

    class FakeTrainer:
        def train(self, df, symbol, model_version):
            if cfg.train_raises:
                raise cfg.train_raises
            return SimpleNamespace(
                metrics=dict(cfg.metrics),
                validation_metrics=dict(cfg.validation_metrics),
                config={}, training_samples=len(df),
            )

    monkeypatch.setattr(trainer_mod, "ModelTrainer", FakeTrainer)

    import app.evaluation.baselines as baselines_mod

    class FakeBaselines:
        def run_all(self, df):
            return {"buy_and_hold": SimpleNamespace(metrics={"sharpe_ratio": cfg.baseline_sharpe})}

    monkeypatch.setattr(baselines_mod, "BaselineStrategies", FakeBaselines)

    import app.governance.versioning as versioning_mod
    import app.governance.lineage as lineage_mod
    monkeypatch.setattr(versioning_mod, "DatasetVersioning", lambda: SimpleNamespace())
    monkeypatch.setattr(lineage_mod, "LineageTracker", lambda: SimpleNamespace(record_training=lambda **k: None))

    import app.models.model_cache as cache_mod
    monkeypatch.setattr(cache_mod, "cache_model", lambda *a, **k: None)
    return cfg


def run(symbols, force=False):
    import app.api.training as training
    return asyncio.run(training.run_training_job(symbols, force=force))
