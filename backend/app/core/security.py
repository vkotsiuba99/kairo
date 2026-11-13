import base64
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.core.config import settings


def _get_aes_key() -> bytes:
    key_str = settings.ENCRYPTION_KEY.strip()
    try:
        # Try hex decoding
        key_bytes = bytes.fromhex(key_str)
        if len(key_bytes) == 32:
            return key_bytes
    except ValueError:
        pass

    try:
        # Try base64 decoding
        key_bytes = base64.b64decode(key_str)
        if len(key_bytes) == 32:
            return key_bytes
    except Exception:
        pass

    # Fallback: pad or truncate utf-8 bytes to 32 bytes
    raw_bytes = key_str.encode("utf-8")
    if len(raw_bytes) >= 32:
        return raw_bytes[:32]
    return raw_bytes.ljust(32, b"0")


def encrypt_credentials(data: dict) -> str:
    """
    Encrypts a credentials dictionary using AES-256-GCM.
    Returns a URL-safe base64 string formatted as nonce (12 bytes) + ciphertext + tag.
    """
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    plaintext = json.dumps(data).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_credentials(encrypted_str: str) -> dict:
    """
    Decrypts an AES-256-GCM encrypted string into a dictionary.
    """
    if not encrypted_str:
        return {}
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encrypted_str.encode("utf-8"))
    nonce = raw[:12]
    ciphertext = raw[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))
