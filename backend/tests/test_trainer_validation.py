"""Trainer: real out-of-sample model metrics, full-series features, purge, alignment."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.models.training.trainer import ModelTrainer


def _df(n=600, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + drift + rng.normal(0, 0.005, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": rng.uniform(5, 15, n)}, index=idx)


class ConstModel:
    """Predicts the same probabilities for every row."""
    def __init__(self, up, down):
        self.up, self.down = up, down

    def predict(self, X):
        return [SimpleNamespace(prob_up=self.up, prob_down=self.down) for _ in range(len(X))]


def _val(model, df, start=420, end=510):
    t = ModelTrainer()
    idx = df.index[start:end]
    X = np.zeros((len(idx), 3))
    y = {"up": np.zeros(len(idx)), "down": np.zeros(len(idx))}
    return t._validate(model, X, y, df, idx)


def test_flat_model_has_zero_sharpe_and_return():
    m = _val(ConstModel(0.3, 0.3), _df(drift=0.002))
    assert m["sharpe_ratio"] == 0.0 and m["total_return"] == 0.0
    assert m["buy_hold_sharpe"] > 0  # the market went up; the old code reported THIS as the model's Sharpe


def test_always_long_tracks_market_minus_fee():
    df = _df(drift=0.002)
    m = _val(ConstModel(0.9, 0.1), df)
    assert m["sharpe_ratio"] > 0
    assert m["sharpe_ratio"] == pytest.approx(m["buy_hold_sharpe"], rel=0.05)


def test_always_short_in_uptrend_is_negative():
    m = _val(ConstModel(0.1, 0.9), _df(drift=0.002))
    assert m["sharpe_ratio"] < 0 and m["total_return"] < 0 and m["max_drawdown"] > 0


def test_accuracy_uses_forward_engine_rule():
    up = _val(ConstModel(0.9, 0.1), _df(drift=0.004))
    down = _val(ConstModel(0.1, 0.9), _df(drift=0.004))
    assert up["accuracy"] > 0.9 and down["accuracy"] < 0.1


def test_empty_validation():
    m = ModelTrainer()._validate(ConstModel(0.9, 0.1), np.zeros((0, 3)), {"up": [], "down": []})
    assert m["val_samples"] == 0 and m["sharpe_ratio"] == 0.0


def test_xy_aligns_by_index_when_middle_rows_dropped():
    idx = pd.date_range("2026-01-01", periods=10, freq="h")
    feat = pd.DataFrame({"a": np.arange(10.0)}, index=idx)
    feat.iloc[3, 0] = np.nan  # dropped in the middle
    labels = pd.DataFrame({"prob_up_4": np.arange(10), "prob_down_4": 0, "forward_return": 0.0}, index=idx)
    X, y, out_idx = ModelTrainer._xy(feat, labels, pd.Series(0.0, index=idx), idx)
    assert list(X[:, 0]) == list(y["up"])  # feature value == label value row by row
    assert 3 not in list(y["up"])


def test_full_training_run_out_of_sample(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import app.models.xgboost_model as xm
    monkeypatch.setattr(xm.XGBoostDecisionModel, "save", lambda self, p: None)
    res = ModelTrainer().train(_df(n=600), "BTCUSDT", model_version="t1")
    v = res.validation_metrics
    # old code: features recomputed on the 90-row val slice -> ~12 samples
    assert v["val_samples"] >= 60
    for k in ("sharpe_ratio", "max_drawdown", "accuracy", "total_return", "buy_hold_sharpe"):
        assert k in v and np.isfinite(v[k])
    assert res.metrics["in_sample"] is True
    assert res.config["val_window"] == [420, 510]
    assert res.config["purge_rows"] >= 4


def test_training_rows_are_purged_before_validation(monkeypatch):
    t = ModelTrainer()
    seen = {}
    real = ModelTrainer._xy

    def spy(feat, labels, vol, rows):
        seen.setdefault("calls", []).append(rows)
        return real(feat, labels, vol, rows)

    monkeypatch.setattr(ModelTrainer, "_xy", staticmethod(spy))
    import app.models.xgboost_model as xm
    monkeypatch.setattr(xm.XGBoostDecisionModel, "save", lambda self, p: None)
    df = _df(n=600)
    t.train(df, "BTCUSDT", model_version="t2")
    train_rows, val_rows = seen["calls"]
    gap = df.index.get_loc(val_rows[0]) - df.index.get_loc(train_rows[-1])
    assert gap > t._label_horizon()  # no training label can see validation prices


def test_baselines_scored_on_same_window():
    from app.evaluation.baselines import BaselineStrategies
    df = _df(n=600, drift=0.001)
    res = BaselineStrategies().run_all_window(df, 420, 510)
    bh = res["buy_and_hold"].metrics
    assert bh["scored_window"] == [420, 510]
    full = BaselineStrategies().run_all(df)["buy_and_hold"].metrics
    assert bh["total_return"] != pytest.approx(full["total_return"])
