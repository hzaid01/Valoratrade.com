"""Forward prediction lifecycle: PENDING -> RESOLVED | EXPIRED."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.evaluation.forward_engine import (
    ForwardEngine, PredictionStatus as P, PREDICTION_TRANSITIONS, EXPIRE_AFTER,
)

SYM = "BTCUSDT"


def _age(fake_db, pred_id, hours):
    ref = fake_db.collection("predictions").document(pred_id)
    ref.update({"timestamp": datetime.utcnow() - timedelta(hours=hours)})


def _doc(fake_db, pred_id):
    return fake_db.raw("predictions", pred_id)


def test_logged_prediction_is_pending(fake_db):
    rec = ForwardEngine().log_prediction(SYM, "long", 0.8, 100.0, "v1", position_size=1.0)
    d = _doc(fake_db, rec.id)
    assert d["status"] == "pending" and d["is_resolved"] is False and d["position_size"] == 1.0


def test_not_resolved_before_maturity(fake_db):
    fe = ForwardEngine()
    fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    assert fe.resolve_pending(lambda s, t: 110.0) == []


def test_resolved_after_maturity_and_persisted(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    seen = []
    [res] = fe.resolve_pending(lambda s, t: seen.append(t) or 110.0)
    assert res.outcome == "win" and res.exit_price == 110.0 and res.status == "resolved"
    d = _doc(fake_db, rec.id)
    assert d["status"] == "resolved" and d["is_resolved"] is True and d["actual_return"] == pytest.approx(0.1)
    # exit price requested at maturity (timestamp + 24h), not "now"
    assert abs((datetime.utcnow() - seen[0]) - timedelta(hours=1)) < timedelta(minutes=1)


def test_resolution_is_idempotent(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "short", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    assert len(fe.resolve_pending(lambda s, t: 90.0)) == 1
    assert fe.resolve_pending(lambda s, t: 50.0) == []
    assert _doc(fake_db, rec.id)["exit_price"] == 90.0


def test_other_instance_resolves(fake_db):
    """Old bug: only this process's in-memory predictions were ever considered."""
    rec = ForwardEngine().log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    assert len(ForwardEngine().resolve_pending(lambda s, t: 101.0)) == 1


def test_two_instances_resolve_exactly_once(fake_db):
    rec = ForwardEngine().log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    a, b = ForwardEngine(), ForwardEngine()
    assert len(a.resolve_pending(lambda s, t: 101.0)) + len(b.resolve_pending(lambda s, t: 101.0)) == 1


def test_no_price_stays_pending_then_expires(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    assert fe.resolve_pending(lambda s, t: None) == []
    assert _doc(fake_db, rec.id)["status"] == "pending"

    _age(fake_db, rec.id, 24 + EXPIRE_AFTER.total_seconds() / 3600 + 1)
    assert fe.resolve_pending(lambda s, t: None) == []
    assert _doc(fake_db, rec.id)["status"] == "expired"
    # terminal: a later price does not resurrect it
    assert fe.resolve_pending(lambda s, t: 120.0) == []
    assert _doc(fake_db, rec.id)["status"] == "expired"


def test_price_fetch_exception_treated_as_missing(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)

    def boom(s, t):
        raise RuntimeError("down")

    assert fe.resolve_pending(boom) == []
    assert _doc(fake_db, rec.id)["status"] == "pending"


def test_transition_table():
    assert PREDICTION_TRANSITIONS[P.PENDING] == {P.RESOLVED, P.EXPIRED}
    assert PREDICTION_TRANSITIONS[P.RESOLVED] == set()
    assert PREDICTION_TRANSITIONS[P.EXPIRED] == set()


@pytest.mark.parametrize("src,dst", [(P.RESOLVED, P.EXPIRED), (P.EXPIRED, P.RESOLVED),
                                     (P.RESOLVED, P.RESOLVED), (P.EXPIRED, P.EXPIRED)])
def test_illegal_finish_is_noop(fake_db, src, dst):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    fake_db.collection("predictions").document(rec.id).update({"status": src.value, "is_resolved": True})
    assert fe._finish(rec.id, dst, {}) is None
    assert _doc(fake_db, rec.id)["status"] == src.value


def test_legacy_docs_without_status(fake_db):
    col = fake_db.collection("predictions")
    old = datetime.utcnow() - timedelta(hours=30)
    base = dict(symbol=SYM, direction="long", confidence=0.7, entry_price=100.0, model_version="v0", timestamp=old)
    col.document("legacy_pending").set(dict(base, id="legacy_pending", is_resolved=False))
    col.document("legacy_done").set(dict(base, id="legacy_done", is_resolved=True, actual_return=0.1, outcome="win"))
    resolved = ForwardEngine().resolve_pending(lambda s, t: 105.0)
    assert [r.id for r in resolved] == ["legacy_pending"]


def test_tz_aware_timestamps(fake_db):
    fake_db.collection("predictions").document("tz").set(dict(
        id="tz", symbol=SYM, direction="long", confidence=0.7, entry_price=100.0,
        model_version="v0", is_resolved=False,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=30),
    ))
    assert len(ForwardEngine().resolve_pending(lambda s, t: 105.0)) == 1


def test_resolve_outcomes_compat_current_prices(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 25)
    assert fe.resolve_outcomes({"ETHUSDT": 1.0}) == []
    assert len(fe.resolve_outcomes({SYM: 102.0})) == 1


# ── metrics ─────────────────────────────────────────────────────────

def _resolved(fe, fake_db, direction, exit_price, version="v1", shadow=False):
    rec = fe.log_prediction(SYM, direction, 0.8, 100.0, version, shadow=shadow)
    _age(fake_db, rec.id, 25)
    fe.resolve_pending(lambda s, t: exit_price)
    return rec


def test_metrics_from_firestore(fake_db):
    fe = ForwardEngine()
    _resolved(fe, fake_db, "long", 110.0)
    _resolved(fe, fake_db, "long", 90.0)
    fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")  # pending
    m = ForwardEngine().get_forward_metrics("v1", days=7)
    assert m.resolved_predictions == 2 and m.pending_predictions == 1 and m.accuracy == 0.5


def test_metrics_exclude_expired(fake_db):
    fe = ForwardEngine()
    rec = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    _age(fake_db, rec.id, 1)
    fake_db.collection("predictions").document(rec.id).update({"status": "expired", "is_resolved": True})
    m = fe.get_forward_metrics("v1")
    assert m.total_predictions == 0


def test_no_trade_scored_like_hold(fake_db):
    fe = ForwardEngine()
    _resolved(fe, fake_db, "no_trade", 120.0)
    assert fe.get_forward_metrics("v1").accuracy == 1.0


def test_shadow_excluded_from_all_but_counted_for_its_version(fake_db):
    fe = ForwardEngine()
    _resolved(fe, fake_db, "long", 110.0, version="champ")
    _resolved(fe, fake_db, "long", 110.0, version="chal", shadow=True)
    assert fe.get_forward_metrics(None).resolved_predictions == 1
    assert fe.get_forward_metrics("chal").resolved_predictions == 1


# ── forward cycle wiring: equity exactly once ───────────────────────

def test_forward_cycle_resolves_and_updates_equity_once(fake_db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app.evaluation.forward_cycle import run_forward_cycle, apply_resolved_to_equity
    from app.capital.controller import CapitalController
    from app.models.registry.model_registry import ModelRegistry
    from app.models.registry.champion_challenger import ChampionChallenger
    from app.core.system_state import get_state_manager

    fe = ForwardEngine()
    served = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1", position_size=2.0)
    shadow = fe.log_prediction(SYM, "long", 0.8, 100.0, "chal", shadow=True)
    no_size = fe.log_prediction(SYM, "long", 0.8, 100.0, "v1")
    for r in (served, shadow, no_size):
        _age(fake_db, r.id, 25)

    ctl = CapitalController()
    reg = ModelRegistry()
    kwargs = dict(
        symbols=[SYM], forward_engine=fe, registry=reg,
        champion_challenger=ChampionChallenger(reg, SimpleNamespace()),
        state_manager=get_state_manager(), price_at=lambda s, t: 110.0,
        on_resolved=lambda resolved: apply_resolved_to_equity(ctl, resolved),
    )
    summary = run_forward_cycle(**kwargs)
    assert summary["resolved"] == 3
    assert ctl.equity_state.equity == 100020.0  # only the served, sized prediction
    run_forward_cycle(**kwargs)
    assert ctl.equity_state.equity == 100020.0  # not applied twice


def test_fetch_binance_close_at(monkeypatch):
    import requests
    from app.evaluation.forward_cycle import fetch_binance_close_at
    when = datetime(2026, 1, 1, 12, 30)
    start = int(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)

    class Resp:
        def __init__(self, rows): self.rows = rows
        def raise_for_status(self): pass
        def json(self): return self.rows

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp([[start, "1", "2", "0.5", "105.5", "9", start + 3599999]]))
    assert fetch_binance_close_at(SYM, when) == 105.5
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp([]))
    assert fetch_binance_close_at(SYM, when) is None
    future_close = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp([[start, "1", "2", "0.5", "105.5", "9", future_close]]))
    assert fetch_binance_close_at(SYM, when) is None  # candle still open
