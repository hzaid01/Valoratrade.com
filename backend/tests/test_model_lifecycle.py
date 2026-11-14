"""Model lifecycle (registry) + champion/challenger promotion + forward cycle."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.registry.model_registry import (
    ModelRegistry, ModelStatus as MS, MODEL_TRANSITIONS, IllegalModelTransition, RetiredReason,
)
from app.models.registry.champion_challenger import ChampionChallenger, PromotionStatus
from app.core.system_state import SystemState as S, get_state_manager

SYM = "BTCUSDT"
GOOD_VAL = {"accuracy": 0.6, "sharpe_ratio": 1.5, "max_drawdown": 0.05}


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _reg():
    return ModelRegistry()


def _register(reg, v, val=None, baseline_summary=None):
    return reg.register(
        version=v, symbol=SYM, patch_tst_path="p", xgboost_path=f"x_{v}",
        dataset_version="d", feature_version="f", training_snapshot="s",
        training_metrics={}, validation_metrics=dict(val or GOOD_VAL),
        baseline_summary=baseline_summary,
    )


def _audits(db):
    return [d.to_dict() for d in db.collection("model_transitions").stream()]


def _baselines(sharpe=0.1):
    return {"buy_and_hold": SimpleNamespace(metrics={"sharpe_ratio": sharpe})}


# ── registry transition table ───────────────────────────────────────

def test_register_starts_registered():
    m = _register(_reg(), "v1")
    assert m.status == MS.REGISTERED.value


@pytest.mark.parametrize("src,dst", [(a, b) for a, t in MODEL_TRANSITIONS.items() for b in t],
                         ids=lambda s: s.value)
def test_every_valid_status_transition(src, dst):
    models = {"m": SimpleNamespace(status=src.value, symbol=SYM, promoted_at=None,
                                   retired_at=None, retired_reason=None)}
    champions, audit = {}, []
    ModelRegistry._apply_status(models, champions, audit, "m", dst, "t", "r")
    assert models["m"].status == dst.value


ILLEGAL = [(a, b) for a in MS for b in MS if b not in MODEL_TRANSITIONS[a] and a != b]


@pytest.mark.parametrize("src,dst", ILLEGAL, ids=lambda s: s.value)
def test_illegal_status_transitions_raise(src, dst):
    models = {"m": SimpleNamespace(status=src.value, symbol=SYM, promoted_at=None,
                                   retired_at=None, retired_reason=None)}
    with pytest.raises(IllegalModelTransition):
        ModelRegistry._apply_status(models, {}, [], "m", dst, "t", "r")


def test_promote_supersedes_previous_champion(fake_db):
    reg = _reg()
    _register(reg, "v1"); _register(reg, "v2")
    assert reg.promote_to_champion(f"{SYM}_v1")
    assert reg.promote_to_champion(f"{SYM}_v2")
    old = reg.get(f"{SYM}_v1")
    assert old.status == "retired" and old.retired_reason == "superseded"
    assert reg.get_champion(SYM).model_id == f"{SYM}_v2"


def test_promote_is_idempotent(fake_db):
    reg = _reg()
    _register(reg, "v1")
    reg.promote_to_champion(f"{SYM}_v1")
    n = len(_audits(fake_db))
    assert reg.promote_to_champion(f"{SYM}_v1") is True
    assert len(_audits(fake_db)) == n


def test_retired_model_cannot_be_promoted_without_override(fake_db, caplog):
    reg = _reg()
    _register(reg, "v1"); _register(reg, "v2")
    reg.promote_to_champion(f"{SYM}_v1"); reg.promote_to_champion(f"{SYM}_v2")
    assert reg.promote_to_champion(f"{SYM}_v1") is False
    assert reg.get_champion(SYM).model_id == f"{SYM}_v2"

    with caplog.at_level("WARNING"):
        assert reg.promote_to_champion(f"{SYM}_v1", override=True, actor="admin", reason="test") is True
    assert "ADMIN OVERRIDE" in caplog.text
    assert reg.get_champion(SYM).model_id == f"{SYM}_v1"
    assert any(a["override"] and a["model_id"] == f"{SYM}_v1" for a in _audits(fake_db))


def test_rejected_model_cannot_be_promoted_without_override():
    reg = _reg()
    _register(reg, "v1")
    reg.record_evaluation(f"{SYM}_v1", PromotionStatus.REJECTED_BASELINES.value)
    assert reg.get(f"{SYM}_v1").status == "rejected"
    assert reg.promote_to_champion(f"{SYM}_v1") is False


def test_unknown_model_promotion_false():
    assert _reg().promote_to_champion("nope") is False


def test_stale_instance_cannot_revert_promotion(fake_db):
    """Old bug: each instance wrote its whole in-memory registry."""
    a = _reg()
    _register(a, "v1")
    b = _reg()  # loaded now: sees v1 as registered
    a.promote_to_champion(f"{SYM}_v1")
    _register(b, "v2")  # b's snapshot is stale
    fresh = _reg()
    assert fresh.get_champion(SYM).model_id == f"{SYM}_v1"
    assert fresh.get(f"{SYM}_v2") is not None


def test_legacy_docs_load(fake_db):
    fake_db.collection("system").document("model_registry").set({
        "models": {f"{SYM}_old": {
            "model_id": f"{SYM}_old", "version": "old", "symbol": SYM,
            "created_at": "2026-03-08T09:00:00", "paths": {"patch_tst": "p", "xgboost": "x"},
            "lineage": {"dataset": "d", "features": "f", "snapshot": "s"},
            "metrics": {"training": {}, "validation": {}, "forward": None},
            "status": "retired", "promoted_at": "2026-03-08T10:00:00",
            "retired_at": "2026-03-09T00:00:00",
        }},
        "champions": {},
    })
    reg = _reg()
    assert reg.get(f"{SYM}_old").status == "retired"
    assert reg.find_rollback_target(SYM) == f"{SYM}_old"  # legacy retired champion = superseded


# ── rollback target ─────────────────────────────────────────────────

def test_rollback_target_only_former_good_champions():
    reg = _reg()
    for v in ("good_old", "champ", "rejected", "pending", "fresh"):
        _register(reg, v)
    reg.promote_to_champion(f"{SYM}_good_old")
    reg.promote_to_champion(f"{SYM}_champ")  # good_old -> retired/superseded
    reg.record_evaluation(f"{SYM}_rejected", "rejected_does_not_beat_baselines")
    reg.record_evaluation(f"{SYM}_pending", "pending_forward_test")
    assert reg.find_rollback_target(SYM, exclude=f"{SYM}_champ") == f"{SYM}_good_old"


def test_rollback_target_never_demoted_or_failed():
    reg = _reg()
    _register(reg, "a"); _register(reg, "b")
    reg.promote_to_champion(f"{SYM}_a")
    reg.retire(f"{SYM}_a", reason="demoted")
    reg.record_evaluation(f"{SYM}_b", "rejected_does_not_beat_baselines")
    assert reg.find_rollback_target(SYM) is None


def test_demotion_rolls_back_to_superseded_champion(fake_db):
    from app.models.registry.demotion import ModelDemotion
    reg = _reg()
    _register(reg, "a"); _register(reg, "b"); _register(reg, "failed")
    reg.promote_to_champion(f"{SYM}_a")
    reg.promote_to_champion(f"{SYM}_b")
    reg.record_evaluation(f"{SYM}_failed", "rejected_does_not_beat_baselines")

    d = ModelDemotion()
    event = d._trigger_demotion(f"{SYM}_b", SYM, "test degradation", {})
    assert event.rolled_back_to == f"{SYM}_a"
    fresh = _reg()
    assert fresh.get_champion(SYM).model_id == f"{SYM}_a"
    assert fresh.get(f"{SYM}_b").retired_reason == "demoted"


def test_demotion_without_target_goes_degraded():
    from app.models.registry.demotion import ModelDemotion
    reg = _reg()
    _register(reg, "a"); _register(reg, "failed")
    reg.promote_to_champion(f"{SYM}_a")
    reg.record_evaluation(f"{SYM}_failed", "rejected_does_not_beat_baselines")
    get_state_manager().override(SYM, S.PROMOTED, "seed", "seed")
    d = ModelDemotion()
    assert d._trigger_demotion(f"{SYM}_a", SYM, "x", {}).rolled_back_to is None
    assert get_state_manager().get_state(SYM).state == S.LIVE_DEGRADED


# ── champion / challenger ───────────────────────────────────────────

def _cc(reg):
    return ChampionChallenger(reg, SimpleNamespace())


def test_challenger_without_forward_metrics_is_pending():
    reg = _reg()
    _register(reg, "champ"); reg.promote_to_champion(f"{SYM}_champ")
    _register(reg, "chal")
    res = _cc(reg).evaluate_challenger(f"{SYM}_chal", _baselines())
    assert res.status == PromotionStatus.PENDING
    m = reg.get(f"{SYM}_chal")
    assert m.status == "pending" and m.baseline_summary == {"buy_and_hold": 0.1}


def test_pending_challenger_promoted_after_forward_metrics():
    reg = _reg()
    _register(reg, "champ"); reg.promote_to_champion(f"{SYM}_champ")
    _register(reg, "chal")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_chal", _baselines())
    reg.update_forward_metrics(f"{SYM}_chal", {"accuracy": 0.6})
    [res] = cc.reevaluate_pending(SYM)
    assert res.status == PromotionStatus.PROMOTED
    assert reg.get_champion(SYM).model_id == f"{SYM}_chal"
    assert reg.get(f"{SYM}_champ").retired_reason == "superseded"


def test_pending_challenger_rejected_on_bad_forward():
    reg = _reg()
    _register(reg, "champ"); reg.promote_to_champion(f"{SYM}_champ")
    _register(reg, "chal")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_chal", _baselines())
    reg.update_forward_metrics(f"{SYM}_chal", {"accuracy": 0.1})
    [res] = cc.reevaluate_pending(SYM)
    assert res.status == PromotionStatus.REJECTED_FORWARD
    assert reg.get(f"{SYM}_chal").status == "rejected"


def test_pending_without_forward_metrics_not_reevaluated():
    reg = _reg()
    _register(reg, "chal")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_chal", _baselines(), promote=False)
    assert cc.reevaluate_pending(SYM) == []


def test_first_model_forward_test_can_promote():
    reg = _reg()
    _register(reg, "first")
    cc = _cc(reg)
    assert cc.evaluate_challenger(f"{SYM}_first", _baselines()).status == PromotionStatus.PENDING
    reg.update_forward_metrics(f"{SYM}_first", {"accuracy": 0.6})
    [res] = cc.reevaluate_pending(SYM)
    assert res.status == PromotionStatus.PROMOTED


# ── training flow: first-model gate ─────────────────────────────────

@pytest.fixture
def harness(monkeypatch, tmp_path):
    from tests import training_harness as h
    cfg = h.install(monkeypatch, tmp_path)
    cached = []
    import app.models.model_cache as cache_mod
    monkeypatch.setattr(cache_mod, "cache_model", lambda s, m: cached.append(s))
    import app.models.xgboost_model as xm
    monkeypatch.setattr(xm.XGBoostDecisionModel, "load", staticmethod(lambda p: SimpleNamespace(path=p)))
    get_state_manager().override(SYM, S.READY_FOR_TRAINING, "seed", "seed")
    return h, cfg, cached


def test_first_model_log_only_promotes_provisionally(harness):
    h, _, cached = harness
    res = h.run([SYM])
    assert res[SYM]["promoted"] is True
    m = ModelRegistry().get_champion(SYM)
    assert m.provisional is True and m.promotion_status == "pending_forward_test"
    assert cached == [SYM]


def test_first_model_enforce_waits_for_forward_test(harness, monkeypatch):
    h, _, _ = harness
    monkeypatch.setenv("FIRST_MODEL_GATE", "enforce")
    res = h.run([SYM])
    assert res[SYM]["promoted"] is False
    assert ModelRegistry().get_champion(SYM) is None
    assert get_state_manager().get_state(SYM).state == S.FORWARD_TESTING


def test_first_model_enforce_rejected(harness, monkeypatch):
    h, cfg, _ = harness
    monkeypatch.setenv("FIRST_MODEL_GATE", "enforce")
    cfg.baseline_sharpe = 100.0
    h.run([SYM])
    assert get_state_manager().get_state(SYM).state == S.REJECTED


def test_later_promotion_refreshes_model_cache(harness, monkeypatch):
    """Old bug: only the first model was cached; later champions served the old model."""
    h, cfg, cached = harness
    h.run([SYM])
    reg = ModelRegistry()
    first = reg.get_champion(SYM)
    reg.update_forward_metrics(first.model_id, {"accuracy": 0.6})
    # make the next challenger pass immediately: give it forward metrics up front
    import app.models.registry.champion_challenger as cc_mod
    real = cc_mod.ChampionChallenger._determine_status
    monkeypatch.setattr(cc_mod.ChampionChallenger, "_determine_status",
                        lambda self, checks: PromotionStatus.PROMOTED)
    res = h.run([SYM])
    assert res[SYM]["promoted"] is True
    assert cached == [SYM, SYM]


# ── forward cycle ───────────────────────────────────────────────────

class FakeForward:
    def __init__(self, metrics_by_version):
        self.metrics = metrics_by_version
        self.resolved_calls = []

    def resolve_outcomes(self, prices):
        self.resolved_calls.append(prices)
        return []

    def get_forward_metrics(self, model_version=None, days=7):
        acc, n = self.metrics.get(model_version, (0.0, 0))
        return SimpleNamespace(resolved_predictions=n,
                               to_dict=lambda: {"accuracy": acc, "resolved_predictions": n})


def test_forward_cycle_promotes_pending_and_syncs_state(monkeypatch):
    from app.evaluation.forward_cycle import run_forward_cycle
    monkeypatch.setenv("MIN_FORWARD_SAMPLES", "5")
    reg = _reg()
    _register(reg, "first")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_first", _baselines(), promote=False)
    get_state_manager().override(SYM, S.FORWARD_TESTING, "seed", "seed")
    promoted = []
    summary = run_forward_cycle(
        [SYM], FakeForward({"first": (0.6, 10)}), reg, cc, get_state_manager(),
        price_fetcher=lambda s: 100.0, on_promoted=lambda s, m: promoted.append(m.model_id),
    )
    assert summary["forward_metrics_updated"] == [f"{SYM}_first"]
    assert summary["evaluations"] == [{"model_id": f"{SYM}_first", "status": "promoted"}]
    assert get_state_manager().get_state(SYM).state == S.PROMOTED
    assert promoted == [f"{SYM}_first"]


def test_forward_cycle_needs_min_samples(monkeypatch):
    from app.evaluation.forward_cycle import run_forward_cycle
    monkeypatch.setenv("MIN_FORWARD_SAMPLES", "20")
    reg = _reg()
    _register(reg, "first")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_first", _baselines(), promote=False)
    summary = run_forward_cycle([SYM], FakeForward({"first": (0.6, 3)}), reg, cc,
                                get_state_manager(), price_fetcher=lambda s: 100.0)
    assert summary["forward_metrics_updated"] == [] and summary["evaluations"] == []


def test_forward_cycle_rejection_without_champion_sets_rejected(monkeypatch):
    from app.evaluation.forward_cycle import run_forward_cycle
    monkeypatch.setenv("MIN_FORWARD_SAMPLES", "1")
    reg = _reg()
    _register(reg, "first")
    cc = _cc(reg)
    cc.evaluate_challenger(f"{SYM}_first", _baselines(), promote=False)
    get_state_manager().override(SYM, S.FORWARD_TESTING, "seed", "seed")
    run_forward_cycle([SYM], FakeForward({"first": (0.1, 5)}), reg, cc,
                      get_state_manager(), price_fetcher=lambda s: 100.0)
    assert get_state_manager().get_state(SYM).state == S.REJECTED


def test_forward_cycle_price_failure_is_reported(monkeypatch):
    from app.evaluation.forward_cycle import run_forward_cycle

    def boom(s):
        raise RuntimeError("binance down")

    ff = FakeForward({})
    summary = run_forward_cycle([SYM], ff, _reg(), _cc(_reg()), get_state_manager(), price_fetcher=boom)
    assert SYM in summary["price_errors"] and ff.resolved_calls == [{}]


# ── shadow predictions ──────────────────────────────────────────────

def test_signals_log_shadow_predictions_for_pending(monkeypatch):
    from tests import signal_harness as sh
    comps = sh.install(monkeypatch)
    reg = _reg()
    _register(reg, "champ"); reg.promote_to_champion(f"{SYM}_champ")
    _register(reg, "chal")
    _cc(reg).evaluate_challenger(f"{SYM}_chal", _baselines())
    comps.model_registry = reg
    import app.models.xgboost_model as xm
    from app.models.xgboost_model import ModelPrediction
    monkeypatch.setattr(xm.XGBoostDecisionModel, "load", staticmethod(
        lambda p: SimpleNamespace(predict_single=lambda x: ModelPrediction(0.2, 0.8, -0.01, 0.8, 0.1))))
    import app.api.signals as signals
    signals._shadow_models.clear()
    get_state_manager().override(SYM, S.PROMOTED, "seed", "seed")
    data = sh.get()["data"]
    assert data["signal"] == "long"  # served signal unaffected
    shadow = [l for l in comps.logged if l.get("shadow")]
    assert shadow and shadow[0]["model_version"] == "chal" and shadow[0]["direction"] == "short"


def test_shadow_failure_never_breaks_signal(monkeypatch):
    from tests import signal_harness as sh
    comps = sh.install(monkeypatch)
    comps.model_registry.list_models = lambda **k: (_ for _ in ()).throw(RuntimeError("x"))
    get_state_manager().override(SYM, S.PROMOTED, "seed", "seed")
    assert sh.get()["data"]["signal"] == "long"


def test_history_hides_shadow_predictions(fake_db):
    from app.evaluation.forward_engine import ForwardEngine
    fe = ForwardEngine()
    fe.log_prediction(SYM, "long", 0.8, 100.0, "champ")
    fe.log_prediction(SYM, "short", 0.8, 100.0, "chal", shadow=True)
    hist = fe.get_prediction_history(SYM)
    assert [h["model_version"] for h in hist] == ["champ"]


# ── admin endpoint ──────────────────────────────────────────────────

def test_admin_promote_requires_override_for_retired(monkeypatch):
    from fastapi import HTTPException
    from app.api import admin
    reg = admin.get_admin_components().model_registry
    _register(reg, "v1"); _register(reg, "v2")
    reg.promote_to_champion(f"{SYM}_v1"); reg.promote_to_champion(f"{SYM}_v2")
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin.promote_model(admin.PromoteRequest(model_id=f"{SYM}_v1")))
    assert e.value.status_code == 400
    out = asyncio.run(admin.promote_model(admin.PromoteRequest(model_id=f"{SYM}_v1", override=True)))
    assert out["success"] is True
