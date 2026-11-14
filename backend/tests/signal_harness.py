"""Run app.api.signals.get_signal with stubbed data/model components."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tests.training_harness import make_df


def install(monkeypatch, champion=True, prediction=None):
    import app.api.signals as signals
    from app.capital.controller import CapitalController
    from app.strategy.signal_generator import SignalGenerator
    from app.core.regime_detector import RegimeState, MarketRegime
    from app.models.xgboost_model import ModelPrediction

    df = make_df(300)
    pred = prediction or ModelPrediction(
        prob_up=0.9, prob_down=0.1, expected_return=0.05,
        confidence=0.9, volatility_score=0.1,
    )

    class Pipeline:
        async def get_decision_data(self, symbol, limit=500):
            return SimpleNamespace(df=df, latest_close=float(df["close"].iloc[-1]))

    logged = []
    controller = CapitalController()
    monkeypatch.setattr(controller, "_get_price", lambda s: 100.0)

    comps = SimpleNamespace(
        data_pipeline=Pipeline(),
        feature_engine=SimpleNamespace(
            compute_features=lambda *a, **k: SimpleNamespace(to_numpy=lambda: np.zeros((2, 3)))
        ),
        regime_detector=SimpleNamespace(detect=lambda d: RegimeState(
            MarketRegime.TRENDING_UP, 0.9, 30.0, 1.0, 1.0, pd.Timestamp.utcnow()
        )),
        capital_controller=controller,
        signal_generator=SignalGenerator(controller),
        trade_calculator=SimpleNamespace(
            calculate_support_resistance=lambda d: (90.0, 110.0),
            calculate=lambda **k: SimpleNamespace(stop_loss=95.0, take_profit_1=110.0),
        ),
        position_sizer=SimpleNamespace(calculate=lambda **k: SimpleNamespace(size=0.5)),
        forward_engine=SimpleNamespace(log_prediction=lambda **k: logged.append(k)),
        model_registry=SimpleNamespace(
            get_champion=lambda s: SimpleNamespace(version="v1", xgboost_path="x") if champion else None
        ),
    )
    monkeypatch.setattr(signals, "get_sys_components", lambda: comps)

    import app.models.model_cache as cache_mod
    cache_mod.clear_cache()
    cache_mod.cache_model("BTCUSDT", SimpleNamespace(predict_single=lambda x: pred),
                          path="x", version="v1")
    comps.logged = logged
    return comps


def get(symbol="BTCUSDT"):
    import app.api.signals as signals
    return asyncio.run(signals.get_signal(symbol, user=None))
