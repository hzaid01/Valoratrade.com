"""
Firebase Admin SDK configuration module.
Provides Firestore client and Auth verification.

DESIGN: Fails loudly on initialization errors — no silent fallbacks.
"""
import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, firestore, auth
from functools import lru_cache

logger = logging.getLogger(__name__)


class FirebaseInitError(Exception):
    """Raised when Firebase cannot be initialized."""
    pass


def initialize_firebase():
    """
    Initialize Firebase Admin SDK.
    Uses GOOGLE_APPLICATION_CREDENTIALS environment variable or
    FIREBASE_SERVICE_ACCOUNT_JSON for inline JSON.

    Raises FirebaseInitError on failure — never silently falls back.
    """
    if firebase_admin._apps:
        return  # Already initialized

    # Option 1: Use service account file path
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and os.path.exists(cred_path):
        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase initialized with service account file: {cred_path}")
            return
        except Exception as e:
            raise FirebaseInitError(f"Failed to initialize Firebase from file '{cred_path}': {e}")

    # Option 2: Use inline JSON from environment variable
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        try:
            service_account_info = json.loads(service_account_json)
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase initialized with inline service account JSON.")
            return
        except json.JSONDecodeError as e:
            raise FirebaseInitError(f"Invalid FIREBASE_SERVICE_ACCOUNT_JSON — JSON parse error: {e}")
        except Exception as e:
            raise FirebaseInitError(f"Failed to initialize Firebase from inline JSON: {e}")

    # Option 3: Use default credentials (only works on Cloud Run / GCE)
    is_cloud_run = bool(os.getenv("K_SERVICE"))
    if is_cloud_run:
        try:
            firebase_admin.initialize_app()
            logger.info("Firebase initialized with default Cloud Run credentials.")
            return
        except Exception as e:
            raise FirebaseInitError(f"Cloud Run default credentials failed: {e}")

    # No credentials available — fail loudly
    raise FirebaseInitError(
        "FIREBASE INITIALIZATION FAILED: No credentials found.\n"
        "  Set one of:\n"
        "    1. GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json\n"
        "    2. FIREBASE_SERVICE_ACCOUNT_JSON='{...}'\n"
        "    3. Deploy on Cloud Run with a service account attached.\n"
    )


@lru_cache(maxsize=1)
def get_firestore():
    """
    Get the Firestore client instance (singleton).
    Raises FirebaseInitError if initialization fails.
    """
    initialize_firebase()
    client = firestore.client()
    if client is None:
        raise FirebaseInitError("Firestore client returned None after initialization.")
    logger.info("Firestore client ready.")
    return client


def verify_firebase_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims.

    Args:
        id_token: The Firebase ID token to verify

    Returns:
        dict: Decoded token claims including 'uid', 'email', etc.

    Raises:
        ValueError: If token is invalid or expired
    """
    initialize_firebase()
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except auth.InvalidIdTokenError as e:
        raise ValueError(f"Invalid token: {e}")
    except auth.ExpiredIdTokenError:
        raise ValueError("Token has expired")
    except auth.RevokedIdTokenError:
        raise ValueError("Token has been revoked")
    except Exception as e:
        raise ValueError(f"Token verification failed: {e}")
