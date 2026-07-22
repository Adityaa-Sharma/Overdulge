"""Symmetric encrypt/decrypt for tokens at rest — AES-256-GCM (ADR-0001).

Ciphertext is base64url(nonce || ciphertext_with_tag); a fresh random nonce
is generated per call to `encrypt`, so encrypting the same plaintext twice
yields different ciphertext.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_NONCE_LENGTH = 12
_KEY_LENGTH = 32


class CryptoConfigError(RuntimeError):
    """`TOKEN_ENCRYPTION_KEY` is missing or not a valid 32-byte key."""


class DecryptionError(RuntimeError):
    """Ciphertext is malformed, truncated, or fails AEAD authentication."""


def _load_key() -> bytes:
    settings = get_settings()
    if not settings.token_encryption_key:
        raise CryptoConfigError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        key = base64.b64decode(settings.token_encryption_key, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise CryptoConfigError("TOKEN_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != _KEY_LENGTH:
        raise CryptoConfigError(
            f"TOKEN_ENCRYPTION_KEY must decode to {_KEY_LENGTH} bytes, got {len(key)}"
        )
    return key


def encrypt(plaintext: bytes) -> str:
    """AES-256-GCM encrypt `plaintext`; returns base64url(nonce || ciphertext+tag)."""
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(_load_key()).encrypt(nonce, plaintext, None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(ciphertext: str) -> bytes:
    """Inverse of `encrypt`. Raises `DecryptionError` on tampered/malformed input."""
    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except (ValueError, base64.binascii.Error) as exc:
        raise DecryptionError("ciphertext is not valid base64") from exc
    if len(raw) <= _NONCE_LENGTH:
        raise DecryptionError("ciphertext is too short to contain a nonce and tag")
    nonce, body = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    try:
        return AESGCM(_load_key()).decrypt(nonce, body, None)
    except InvalidTag as exc:
        raise DecryptionError("ciphertext failed authentication") from exc
