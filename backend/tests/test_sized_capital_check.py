"""Capital checks use the real position size, not a 1.0 placeholder."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.capital.controller import CapitalController, Position, RejectionReason
from app.core.system_state import SystemState as S, get_state_manager
from tests import signal_harness as h


def _boom(symbol):
    raise RuntimeError("price API down")


def test_unsized_precheck_skips_price_fetch():
    c = CapitalController()
    c._get_price = _boom
    a = c.can_trade("BTCUSDT", "long", 0.9, None)
    assert a.approved is True  # old code: 1.0 placeholder -> price fetch -> rejected


def test_unsized_precheck_still_enforces_non_size_rules():
    c = CapitalController()
    c._get_price = _boom
    assert c.can_trade("BTCUSDT", "long", 0.1, None).reason == RejectionReason.LOW_CONFIDENCE
    c._trigger_kill("x")
    assert c.can_trade("BTCUSDT", "long", 0.9, None).reason == RejectionReason.KILL_SWITCH_ACTIVE


def test_supplied_price_avoids_network():
    c = CapitalController()
    c._get_price = _boom
    a = c.can_trade("BTCUSDT", "long", 0.9, 1.0, price=100.0)
    assert a.approved and a.adjusted_size == 1.0


def test_real_size_is_capped_by_exposure():
    c = CapitalController()
    a = c.can_trade("BTCUSDT", "long", 0.9, 10_000.0, price=100.0)  # 1,000,000 notional
    assert a.approved and a.adjusted_size == pytest.approx(0.30 * 100000 / 100.0)


def test_correlation_checked_even_when_exposure_adjusts():
    """Old bug: the exposure-adjust branch returned early and skipped correlation."""
    c = CapitalController()
    c.positions["BTCUSDT"] = Position("BTCUSDT", "long", 100.0, 1.0, datetime.utcnow())
    a = c.can_trade("BTCUSDT", "long", 0.9, 10_000.0, price=100.0)
    assert a.approved is False and a.reason == RejectionReason.CORRELATION_LIMIT


def test_signal_position_size_is_capped(monkeypatch):
    comps = h.install(monkeypatch)
    comps.position_sizer = SimpleNamespace(calculate=lambda **k: SimpleNamespace(size=1e9))
    get_state_manager().override("BTCUSDT", S.PROMOTED, "t", "seed")
    data = h.get()["data"]
    assert data["signal"] == "long"
    price = data["trade"]["entry_price"]
    assert data["trade"]["position_size"] == pytest.approx(0.30 * 100000 / price)


def test_signal_rejected_by_sized_check_becomes_no_trade(monkeypatch):
    comps = h.install(monkeypatch)
    c = comps.capital_controller
    c.positions["ETHUSDT"] = Position("ETHUSDT", "long", 100.0, 1000.0, datetime.utcnow())  # 100% exposure
    get_state_manager().override("BTCUSDT", S.PROMOTED, "t", "seed")
    data = h.get()["data"]
    assert data["signal"] == "no_trade"
    assert data["metadata"]["filters_passed"]["rejection_reason"] == "capital_max_exposure_reached"
    assert data["trade"]["position_size"] is None


def test_signal_survives_price_api_outage(monkeypatch):
    comps = h.install(monkeypatch)
    comps.capital_controller._get_price = _boom
    get_state_manager().override("BTCUSDT", S.PROMOTED, "t", "seed")
    assert h.get()["data"]["signal"] == "long"
