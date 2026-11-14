"""
Shared pytest setup: every test gets a fresh in-memory Firestore and fresh
module-level singletons, so no test can reach a real database.
"""
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("ADMIN_SECRET_KEY", "test_admin_secret_12345")
os.environ.setdefault("ENCRYPTION_SECRET", "test_encryption_secret_67890")
os.environ.setdefault("DEBUG", "true")
# Never let a test pick up real credentials.
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON", None)

from tests.fake_firestore import FakeFirestore  # noqa: E402
import app.firebase_config as firebase_config  # noqa: E402

_current = {"db": FakeFirestore()}


def _fake_get_firestore():
    return _current["db"]


# Replace before any app module does `from app.firebase_config import get_firestore`.
firebase_config.get_firestore = _fake_get_firestore
firebase_config.initialize_firebase = lambda: None


def _reset_singletons():
    import importlib
    for mod_name, attr in [
        ("app.core.system_state", "_state_manager"),
        ("app.core.job_lock", "_lock_manager"),
        ("app.capital.controller", "_capital_controller"),
        ("app.api.signals", "_components"),
        ("app.api.admin", "_components"),
    ]:
        try:
            mod = importlib.import_module(mod_name)
            setattr(mod, attr, None)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def fake_db():
    db = FakeFirestore()
    _current["db"] = db
    _reset_singletons()
    yield db
    _reset_singletons()
