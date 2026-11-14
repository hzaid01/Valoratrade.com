"""Kill switch / trading-mode state machine."""
import itertools
from datetime import datetime, timedelta

import pytest

from app.capital.controller import CapitalController, RejectionReason, get_capital_controller
from app.capital.killswitch import (
    KillSwitchState as M, KillSwitchEvent as E, KILLSWITCH_TRANSITIONS,
    IllegalKillSwitchTransition, next_trading_mode, KillSwitch, KillReason,
)

DOC = ("system_state", "capital_controller")


def _ctl(monkeypatch=None):
    c = CapitalController()
    if monkeypatch:
        monkeypatch.setattr(c, "_get_price", lambda s: 100.0)
    return c


def _approve(c):
    return c.can_trade("BTCUSDT", "long", 0.9, 0.01)


# ── pure transition table ───────────────────────────────────────────

EXPECTED = {
    (M.ACTIVE, E.KILL): M.KILLED,
    (M.ACTIVE, E.FLAT_ON): M.FORCED_FLAT,
    (M.ACTIVE, E.FLAT_OFF): M.ACTIVE,
    (M.ACTIVE, E.RESET): M.ACTIVE,
    (M.FORCED_FLAT, E.KILL): M.KILLED,
    (M.FORCED_FLAT, E.FLAT_ON): M.FORCED_FLAT,
    (M.FORCED_FLAT, E.FLAT_OFF): M.ACTIVE,
    (M.FORCED_FLAT, E.RESET): M.FORCED_FLAT,
    (M.KILLED, E.KILL): M.KILLED,
    (M.KILLED, E.RESET): M.ACTIVE,
}


@pytest.mark.parametrize("pair", list(EXPECTED), ids=lambda p: f"{p[0].value}-{p[1].value}")
def test_valid_pure_transitions(pair):
    assert next_trading_mode(*pair) == EXPECTED[pair]


def test_reset_resumes_forced_flat():
    assert next_trading_mode(M.KILLED, E.RESET, resume_mode=M.FORCED_FLAT) == M.FORCED_FLAT


ILLEGAL = [p for p in itertools.product(M, E) if p not in KILLSWITCH_TRANSITIONS]


def test_illegal_pairs_are_exactly_flat_while_killed():
    assert set(ILLEGAL) == {(M.KILLED, E.FLAT_ON), (M.KILLED, E.FLAT_OFF)}


@pytest.mark.parametrize("pair", ILLEGAL, ids=lambda p: f"{p[0].value}-{p[1].value}")
def test_illegal_pure_transitions_raise(pair):
    with pytest.raises(IllegalKillSwitchTransition):
        next_trading_mode(*pair)


# ── persisted single source of truth ────────────────────────────────

def test_kill_persists_trading_mode_and_audit(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("manual test")
    doc = fake_db.raw(*DOC)
    assert doc["trading_mode"] == "killed"
    assert doc["resume_mode"] == "active"
    assert doc["is_killed"] is True  # read-only mirror
    audits = [d.to_dict() for d in fake_db.collection("capital_mode_transitions").stream()]
    assert [(a["from_mode"], a["to_mode"]) for a in audits] == [("active", "killed")]
    assert _approve(c).reason == RejectionReason.KILL_SWITCH_ACTIVE


def test_duplicate_kill_is_idempotent(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("first")
    c._trigger_kill("dup")
    audits = list(fake_db.collection("capital_mode_transitions").stream())
    assert len(audits) == 1
    assert len(c.killswitch.activation_history) == 1
    assert fake_db.raw(*DOC)["mode_reason"] == "first"


def test_reset_returns_to_active(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("x")
    assert c.reset_kill_switch("k", "k") is True
    assert fake_db.raw(*DOC)["trading_mode"] == "active"
    assert _approve(c).approved is True


def test_reset_with_wrong_key_does_nothing(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("x")
    assert c.reset_kill_switch("bad", "k") is False
    assert c.is_killed is True


def test_forced_flat_persisted_and_survives_restart(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c.set_forced_flat(True)
    assert fake_db.raw(*DOC)["trading_mode"] == "forced_flat"
    restarted = _ctl(monkeypatch)
    assert _approve(restarted).reason == RejectionReason.FORCED_FLAT
    restarted.set_forced_flat(False)
    assert _approve(restarted).approved is True


def test_kill_while_flat_then_reset_resumes_flat(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c.set_forced_flat(True)
    c._trigger_kill("x")
    c.reset_kill_switch("k", "k")
    assert c.get_mode() == M.FORCED_FLAT


def test_flat_toggle_while_killed_is_rejected(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("x")
    with pytest.raises(IllegalKillSwitchTransition):
        c.set_forced_flat(True)
    with pytest.raises(IllegalKillSwitchTransition):
        c.set_forced_flat(False)
    assert fake_db.raw(*DOC)["trading_mode"] == "killed"


@pytest.mark.parametrize("legacy,expected", [
    ({"is_killed": True, "is_forced_flat": True}, M.KILLED),
    ({"is_killed": False, "is_forced_flat": True}, M.FORCED_FLAT),
    ({"is_killed": False}, M.ACTIVE),
])
def test_legacy_docs_migrate(fake_db, legacy, expected):
    fake_db.collection(DOC[0]).document(DOC[1]).set(legacy)
    assert CapitalController().get_mode() == expected


def test_kill_on_one_instance_seen_by_another(fake_db, monkeypatch):
    a, b = _ctl(monkeypatch), _ctl(monkeypatch)
    a._trigger_kill("instance A")
    assert b.is_kill_active() is True
    assert _approve(b).reason == RejectionReason.KILL_SWITCH_ACTIVE


def test_equity_save_on_other_instance_cannot_unkill(fake_db, monkeypatch):
    a, b = _ctl(monkeypatch), _ctl(monkeypatch)
    a._trigger_kill("x")
    b.update_equity(100500.0)  # old code wrote is_killed=False from memory here
    assert fake_db.raw(*DOC)["trading_mode"] == "killed"


def test_mode_read_failure_uses_last_known(monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("x")

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("firestore down")

    monkeypatch.setattr(c, "doc_ref", Broken())
    assert c.get_mode() == M.KILLED


def test_shared_controller_between_signals_and_admin(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from app.api import admin
    assert admin.get_admin_components().capital_controller is get_capital_controller()


def test_admin_kill_blocks_signals_controller(monkeypatch, tmp_path):
    """Old bug: admin and signals had separate in-memory controllers."""
    monkeypatch.chdir(tmp_path)
    separate = _ctl(monkeypatch)  # e.g. a signals instance in another process
    get_capital_controller()._trigger_kill("admin")
    assert separate.is_kill_active() is True


# ── daily rollover ──────────────────────────────────────────────────

def test_trades_today_resets_on_new_day(monkeypatch):
    c = _ctl(monkeypatch)
    c.record_closed_trade("BTCUSDT", "long", 100, 101, 1, datetime.utcnow())
    assert c.trades_today == 1
    c.last_reset = datetime.utcnow() - timedelta(days=1)
    assert c.get_status()["trades_today"] == 0
    assert c.last_reset.date() == datetime.utcnow().date()


def test_rejections_today_counted(monkeypatch):
    c = _ctl(monkeypatch)
    c._trigger_kill("x")
    _approve(c)
    assert c.rejections_today == 1


# ── equity flow & automatic activation (KILLSWITCH_AUTO) ────────────

def test_record_closed_trade_updates_equity_and_leaves_no_position(fake_db, monkeypatch):
    c = _ctl(monkeypatch)
    pnl = c.record_closed_trade("BTCUSDT", "short", 100, 90, 2, datetime.utcnow())
    assert pnl == 20
    assert c.equity_state.equity == 100020
    assert fake_db.raw(*DOC)["equity"] == 100020
    assert c.positions == {}
    assert _approve(c).approved is True  # no lingering position to block signals


def test_record_closed_trade_ignores_no_trade(monkeypatch):
    c = _ctl(monkeypatch)
    assert c.record_closed_trade("BTCUSDT", "no_trade", 100, 90, 1, datetime.utcnow()) is None
    assert c.equity_state.equity == 100000


def test_equity_updates_accumulate_across_instances(fake_db, monkeypatch):
    a, b = _ctl(monkeypatch), _ctl(monkeypatch)
    a.record_closed_trade("BTCUSDT", "long", 100, 110, 1, datetime.utcnow())
    b.record_closed_trade("ETHUSDT", "long", 100, 105, 1, datetime.utcnow())
    assert fake_db.raw(*DOC)["equity"] == 100015


def test_auto_drawdown_kill_log_only_by_default(monkeypatch, caplog):
    monkeypatch.delenv("KILLSWITCH_AUTO", raising=False)
    c = _ctl(monkeypatch)
    with caplog.at_level("WARNING"):
        c.update_equity(50000.0)  # 50% drawdown
    assert c.get_mode() == M.ACTIVE
    assert "WOULD HAVE KILLED" in caplog.text
    # can_trade also only logs the drawdown rejection in log_only
    assert _approve(c).approved is True


def test_auto_drawdown_kill_enforced(monkeypatch):
    monkeypatch.setenv("KILLSWITCH_AUTO", "enforce")
    c = _ctl(monkeypatch)
    c.update_equity(50000.0)
    assert c.get_mode() == M.KILLED
    assert c.killswitch.activation_history[-1].reason == KillReason.MAX_DRAWDOWN


def test_can_trade_drawdown_enforced_rejects(monkeypatch):
    monkeypatch.setenv("KILLSWITCH_AUTO", "enforce")
    c = _ctl(monkeypatch)
    c.equity_state.drawdown_pct = 0.5
    assert _approve(c).reason == RejectionReason.MAX_DRAWDOWN_EXCEEDED
    assert c.get_mode() == M.KILLED


def test_consecutive_losses_log_only_then_enforce(monkeypatch):
    c = _ctl(monkeypatch)
    for _ in range(5):
        c.record_closed_trade("BTCUSDT", "long", 100, 99.9, 1, datetime.utcnow())
    assert c.get_mode() == M.ACTIVE
    monkeypatch.setenv("KILLSWITCH_AUTO", "enforce")
    c.record_closed_trade("BTCUSDT", "long", 100, 99.9, 1, datetime.utcnow())
    assert c.get_mode() == M.KILLED
    assert c.killswitch.activation_history[-1].reason == KillReason.CONSECUTIVE_LOSSES


def test_manual_kill_enforced_in_log_only(monkeypatch):
    monkeypatch.setenv("KILLSWITCH_AUTO", "log_only")
    c = _ctl(monkeypatch)
    c._trigger_kill("operator")
    assert c.get_mode() == M.KILLED


def test_unknown_auto_value_is_log_only(monkeypatch):
    monkeypatch.setenv("KILLSWITCH_AUTO", "yes please")
    c = _ctl(monkeypatch)
    c.update_equity(10.0)
    assert c.get_mode() == M.ACTIVE


def test_killswitch_evaluator_is_stateless_about_mode():
    ks = KillSwitch()
    assert ks.is_active is False
    assert ks.evaluate(100.0, api_error=True)[0] == KillReason.API_ERROR
    assert ks.is_active is False  # evaluation never changes the mode


def test_status_shape_unchanged(monkeypatch):
    c = _ctl(monkeypatch)
    st = c.get_status()
    for k in ("is_killed", "is_forced_flat", "equity", "drawdown_pct", "positions_count",
              "total_exposure", "exposure_pct", "trades_today", "rejections_today"):
        assert k in st
