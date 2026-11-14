"""jobs/retrain.py goes through the locked, state-checked training job."""
import asyncio

from app.core.system_state import SystemState as S, get_state_manager


def test_job_delegates_to_run_training_job(monkeypatch):
    import app.api.training as training
    from jobs import retrain
    calls = []

    async def fake(symbols, force=False):
        calls.append((symbols, force))
        return {"ok": True}

    monkeypatch.setattr(training, "run_training_job", fake)
    monkeypatch.setenv("SYMBOLS", "btcusdt, ethusdt")
    assert asyncio.run(retrain.main()) == {"ok": True}
    assert calls == [(["BTCUSDT", "ETHUSDT"], False)]


def test_symbols_default(monkeypatch):
    from jobs import retrain
    monkeypatch.delenv("SYMBOLS", raising=False)
    assert retrain.symbols_from_env() == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_job_respects_state_and_lock(monkeypatch, tmp_path, fake_db):
    """End to end with the harness: NO_DATA symbol is skipped, lock released."""
    from tests import training_harness as h
    from jobs import retrain
    h.install(monkeypatch, tmp_path)
    res = asyncio.run(retrain.RetrainJob().run(["BTCUSDT"]))
    assert res["BTCUSDT"] == {"status": "skipped", "reason": "invalid_state:no_data"}
    assert fake_db.raw("job_locks", "BTCUSDT_training")["locked"] is False
