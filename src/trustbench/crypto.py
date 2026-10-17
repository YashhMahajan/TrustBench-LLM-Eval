"""Encryption of stored content (Fernet: AES-128-CBC with HMAC).

The key comes from TRUSTBENCH_SECRET_KEY. For key rotation, list several keys with commas:
the first key encrypts and all keys can decrypt. Make a key with: trustbench secrets generate-key
"""

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

VERSION = "v1:"


class SecretKeyMissing(RuntimeError):
    pass


class DecryptionError(ValueError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _box() -> MultiFernet:
    raw = os.environ.get("TRUSTBENCH_SECRET_KEY", "").strip()
    if not raw:
        raise SecretKeyMissing("Set TRUSTBENCH_SECRET_KEY to store content. Make one with: trustbench secrets generate-key")
    try:
        return MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])
    except (ValueError, TypeError) as e:
        raise SecretKeyMissing("TRUSTBENCH_SECRET_KEY is not a valid key. Make one with: trustbench secrets generate-key") from e


def available() -> bool:
    try:
        _box()
        return True
    except SecretKeyMissing:
        return False


def encrypt_text(text: str) -> str:
    return VERSION + _box().encrypt(text.encode()).decode()


def decrypt_text(token: str) -> str:
    if not token.startswith(VERSION):
        raise DecryptionError("Unknown format of stored content.")
    try:
        return _box().decrypt(token[len(VERSION):].encode()).decode()
    except InvalidToken as e:
        raise DecryptionError("The content cannot be decrypted with the current key.") from e
