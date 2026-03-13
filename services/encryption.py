"""
Fernet symmetric encryption for sensitive user data (BYOK API keys).
Derives encryption key from Flask SECRET_KEY via PBKDF2.
"""

import base64
import hashlib
from cryptography.fernet import Fernet
from flask import current_app


def _get_fernet():
    """Derive a Fernet key from the Flask SECRET_KEY."""
    secret = current_app.config['SECRET_KEY'].encode()
    # PBKDF2 with SHA-256 → 32 bytes → base64 for Fernet
    dk = hashlib.pbkdf2_hmac('sha256', secret, b'disputegpt-byok-salt', 100_000)
    key = base64.urlsafe_b64encode(dk)
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a ciphertext string back to plaintext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
