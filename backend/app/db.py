"""
Centralized database client module.
Provides Firestore client with proper validation.
"""
from app.firebase_config import get_firestore


def get_db():
    """
    Get the Firestore client instance (singleton).
    Raises FirebaseInitError if initialization fails.
    """
    return get_firestore()
