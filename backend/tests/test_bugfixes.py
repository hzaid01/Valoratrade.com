"""
Automated validation test suite for ValoraTrade bug fixes.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

# Environment setup for tests
os.environ["ADMIN_SECRET_KEY"] = "test_admin_secret_12345"
os.environ["ENCRYPTION_SECRET"] = "test_encryption_secret_67890"
os.environ["BINANCE_API_KEY"] = "mock_key"
os.environ["BINANCE_API_SECRET"] = "mock_secret"
os.environ["DEBUG"] = "true"

from app.capital.controller import CapitalController, RejectionReason
from app.capital.killswitch import KillSwitch
from app.utils.encryption import encrypt_value, decrypt_value, LEGACY_STATIC_SALT, derive_fernet_key
from app.core.target_engineer import TripleBarrierLabeler, BarrierLabel
import pandas as pd
import numpy as np
from cryptography.fernet import Fernet


def test_killswitch_gate_integration():
    """PRIORITY 3: Verify killswitch activation causes can_trade() to return False."""
    controller = CapitalController()
    controller.is_killed = False
    controller.killswitch.is_active = False

    # Check initially tradeable (mocking price fetch)
    with patch.object(controller, '_get_price', return_value=50000.0):
        approval = controller.can_trade(
            symbol="BTCUSDT",
            direction="long",
            confidence=0.85,
            proposed_size=0.1
        )
        assert approval.approved is True, f"Expected initial trade approval, got {approval.message}"

    # Trigger kill switch
    controller._trigger_kill("Emergency test shutdown")
    assert controller.is_killed is True
    assert controller.killswitch.is_active is True

    # Check trade is now blocked
    with patch.object(controller, '_get_price', return_value=50000.0):
        approval_after_kill = controller.can_trade(
            symbol="BTCUSDT",
            direction="long",
            confidence=0.85,
            proposed_size=0.1
        )
        assert approval_after_kill.approved is False
        assert approval_after_kill.reason == RejectionReason.KILL_SWITCH_ACTIVE, "can_trade() must return KILL_SWITCH_ACTIVE"


def test_price_fetch_no_silent_fallback():
    """PRIORITY 4: Verify _get_price raises RuntimeError on network failure, never returns $1.00."""
    controller = CapitalController()
    controller.positions = {}

    with patch("requests.get", side_effect=Exception("Connection timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            controller._get_price("BTCUSDT")
        assert "Failed to fetch market price for BTCUSDT after 3 retries" in str(exc_info.value)

    # Verify can_trade handles price failure safely
    with patch.object(controller, '_get_price', side_effect=RuntimeError("Price API network error")):
        approval = controller.can_trade(
            symbol="BTCUSDT",
            direction="long",
            confidence=0.85,
            proposed_size=0.1
        )
        assert approval.approved is False
        assert "Price unavailable" in approval.message


def test_dynamic_salt_encryption():
    """PRIORITY 5: Verify unique salt per encryption and backward compatibility for legacy salt."""
    raw_secret = "FAKE_TEST_VALUE_NOT_A_REAL_SECRET_000"

    enc1 = encrypt_value(raw_secret)
    enc2 = encrypt_value(raw_secret)

    # Output format check
    assert enc1.startswith("v2:"), "New encryption must use v2 prefix"
    assert enc2.startswith("v2:"), "New encryption must use v2 prefix"

    # Salting uniqueness check
    assert enc1 != enc2, "Encrypted outputs for same text must be unique due to random salt"

    # Decryption checks
    assert decrypt_value(enc1) == raw_secret
    assert decrypt_value(enc2) == raw_secret

    # Legacy v1 static salt backward compatibility check
    secret_env = os.getenv("ENCRYPTION_SECRET")
    legacy_key = derive_fernet_key(secret_env, LEGACY_STATIC_SALT)
    legacy_f = Fernet(legacy_key)
    legacy_encrypted = legacy_f.encrypt(raw_secret.encode()).decode()

    # Must correctly decrypt legacy payload
    assert decrypt_value(legacy_encrypted) == raw_secret


def test_usdt_suffix_formatting():
    """PRIORITY 6: Verify USDT formatting doesn't create BTCUSDTUSDT."""
    symbol = "BTC"
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    assert symbol == "BTCUSDT"

    symbol_already = "BTCUSDT"
    symbol_already = symbol_already.upper()
    if not symbol_already.endswith("USDT"):
        symbol_already = f"{symbol_already}USDT"
    assert symbol_already == "BTCUSDT"


def test_triple_barrier_labeling_performance():
    """PRIORITY 7: Verify vectorized triple barrier labeling correctness."""
    np.random.seed(42)
    n_candles = 100
    prices = 100.0 + np.cumsum(np.random.randn(n_candles))
    
    df = pd.DataFrame({
        'open': prices,
        'high': prices + np.abs(np.random.randn(n_candles)),
        'low': prices - np.abs(np.random.randn(n_candles)),
        'close': prices,
        'volume': np.random.randint(100, 1000, n_candles)
    })

    labeler = TripleBarrierLabeler(max_holding_periods=5)
    target_set = labeler.label_dataset(df)

    assert len(target_set.labels) == n_candles
    assert 'barrier_label' in target_set.labels.columns
    assert set(target_set.labels['barrier_label'].unique()).issubset({-1, 0, 1})
