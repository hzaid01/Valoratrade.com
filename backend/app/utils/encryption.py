"""
Encryption utilities for secure API key storage.
Uses Fernet symmetric encryption from the cryptography library with dynamic per-value salt.
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

LEGACY_STATIC_SALT = b"cryptobot_salt_v1"


def derive_fernet_key(secret: str, salt: bytes) -> bytes:
    """
    Derive a 32-byte urlsafe Fernet key from secret and salt using PBKDF2.
    """
    try:
        if len(secret) == 44 and salt == LEGACY_STATIC_SALT:
            Fernet(secret.encode())
            return secret.encode()
    except Exception:
        pass

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def get_encryption_key() -> bytes:
    """
    Get legacy default encryption key (using static salt).
    """
    secret = os.getenv("ENCRYPTION_SECRET")
    if not secret:
        raise RuntimeError(
            "ENCRYPTION_SECRET environment variable is required for API key encryption. "
            "Generate one using: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return derive_fernet_key(secret, LEGACY_STATIC_SALT)


def encrypt_value(value: str) -> str:
    """
    Encrypt a string value using Fernet encryption with a unique per-value salt.
    Format: v2:<salt_hex>:<ciphertext_base64>
    """
    if not value or value.strip() == "":
        return ""

    secret = os.getenv("ENCRYPTION_SECRET")
    if not secret:
        raise RuntimeError(
            "ENCRYPTION_SECRET environment variable is required for API key encryption."
        )

    try:
        salt = os.urandom(16)
        key = derive_fernet_key(secret, salt)
        f = Fernet(key)
        encrypted = f.encrypt(value.encode()).decode()
        return f"v2:{salt.hex()}:{encrypted}"
    except Exception as e:
        raise RuntimeError(f"Encryption failed: {str(e)}")


def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypt a string value that was encrypted with encrypt_value.
    Supports both new v2 (dynamic salt) and legacy v1 (static salt) payloads.
    """
    if not encrypted_value or encrypted_value.strip() == "":
        return ""

    secret = os.getenv("ENCRYPTION_SECRET")
    if not secret:
        return ""

    try:
        # Check for v2 format (v2:<salt_hex>:<ciphertext>)
        if encrypted_value.startswith("v2:"):
            parts = encrypted_value.split(":", 2)
            if len(parts) == 3:
                salt = bytes.fromhex(parts[1])
                ciphertext = parts[2]
                key = derive_fernet_key(secret, salt)
                f = Fernet(key)
                return f.decrypt(ciphertext.encode()).decode()

        # Legacy v1 attempt (static salt)
        key = derive_fernet_key(secret, LEGACY_STATIC_SALT)
        f = Fernet(key)
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception:
        print("Decryption failed - key may have been corrupted or changed")
        return ""


def mask_api_key(key: str) -> str:
    """
    Mask an API key for display purposes, showing only first 4 and last 4 characters.
    """
    if not key or len(key) < 12:
        return "****" if key else ""
    return f"{key[:4]}...{key[-4:]}"
