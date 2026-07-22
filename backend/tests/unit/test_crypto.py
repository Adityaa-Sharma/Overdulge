import base64

import pytest

from app.core import crypto
from app.core.config import Settings, get_settings

_TEST_KEY = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(token_encryption_key=_TEST_KEY)
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


def test_encrypt_decrypt_round_trip():
    plaintext = b"super-secret-refresh-token"

    ciphertext = crypto.encrypt(plaintext)

    assert crypto.decrypt(ciphertext) == plaintext


def test_encrypt_is_nondeterministic_across_calls():
    plaintext = b"same-plaintext-every-time"

    assert crypto.encrypt(plaintext) != crypto.encrypt(plaintext)


def test_ciphertext_does_not_contain_plaintext():
    plaintext = b"do-not-leak-this-refresh-token"

    ciphertext = crypto.encrypt(plaintext)

    assert plaintext not in ciphertext.encode("ascii")
    assert plaintext.hex() not in ciphertext


def test_decrypt_rejects_tampered_ciphertext():
    ciphertext = crypto.encrypt(b"access-token")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("ascii")))
    raw[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered)


def test_decrypt_rejects_truncated_ciphertext():
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(base64.urlsafe_b64encode(b"short").decode("ascii"))


def test_decrypt_rejects_invalid_base64():
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("not-valid-base64url!!")


def test_encrypt_requires_configured_key(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(token_encryption_key=None))

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt(b"token")


def test_encrypt_rejects_wrong_length_key(monkeypatch):
    bad_key = base64.b64encode(b"too-short").decode("ascii")
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(token_encryption_key=bad_key))

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt(b"token")


def test_encrypt_rejects_non_base64_key(monkeypatch):
    monkeypatch.setattr(
        crypto, "get_settings", lambda: Settings(token_encryption_key="not base64!!")
    )

    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt(b"token")
