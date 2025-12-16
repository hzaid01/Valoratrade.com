"""
Encryption utilities for secure API key storage.
Uses Fernet symmetric encryption from the cryptography library.
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def get_encryption_key() -> bytes:
    """
    Get or derive the encryption key from environment variable.
    The ENCRYPTION_SECRET should be a secure random string.
    """
    secret = os.getenv("ENCRYPTION_SECRET")
    if not secret:
        raise RuntimeError(
            "ENCRYPTION_SECRET environment variable is required for API key encryption. "
            "Generate one using: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    
    # If the secret is already a valid Fernet key (44 bytes base64), use it directly
    try:
        if len(secret) == 44:
            Fernet(secret.encode())
            return secret.encode()
    except Exception:
        pass
    
    # Otherwise, derive a key from the secret using PBKDF2
    salt = b"cryptobot_salt_v1"  # Static salt - in production, use unique salt per key
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return key


def encrypt_value(value: str) -> str:
    """
    Encrypt a string value using Fernet encryption.
    Returns the encrypted value as a string, or empty string if input is empty.
    """
    if not value or value.strip() == "":
        return ""
    
    try:
        key = get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return encrypted.decode()
    except Exception as e:
        raise RuntimeError(f"Encryption failed: {str(e)}")


def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypt a string value that was encrypted with encrypt_value.
    Returns the decrypted value, or empty string if input is empty.
    """
    if not encrypted_value or encrypted_value.strip() == "":
        return ""
    
    try:
        key = get_encryption_key()
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_value.encode())
        return decrypted.decode()
    except Exception as e:
        # Log the error but don't expose details
        print(f"Decryption failed - key may have been corrupted or changed")
        return ""


def mask_api_key(key: str) -> str:
    """
    Mask an API key for display purposes, showing only first 4 and last 4 characters.
    """
    if not key or len(key) < 12:
        return "****" if key else ""
    return f"{key[:4]}...{key[-4:]}"
