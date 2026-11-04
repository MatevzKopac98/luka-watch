"""Password encryption for the private betting data and the locked betting page.

AES-256-GCM with a key derived from the password (PBKDF2-SHA256, 250,000 rounds, random salt).
The page uses the same scheme in the browser (WebCrypto), so only someone with the password can read it.
The password comes from the BETTING_PASSWORD environment variable (a GitHub Secret); it is never stored.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 250_000


def password():
    return os.environ.get("BETTING_PASSWORD") or None


def _key(pw, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(pw.encode())


def encrypt(data: bytes, pw: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return salt + nonce + AESGCM(_key(pw, salt)).encrypt(nonce, data, None)


def decrypt(blob: bytes, pw: str) -> bytes:
    salt, nonce, ct = blob[:16], blob[16:28], blob[28:]
    return AESGCM(_key(pw, salt)).decrypt(nonce, ct, None)


def encrypt_file(src, dst, pw):
    dst.write_bytes(encrypt(src.read_bytes(), pw))


def decrypt_file(src, dst, pw):
    dst.write_bytes(decrypt(src.read_bytes(), pw))


def page_payload(html: str, pw: str, salt: bytes) -> str:
    """JSON the locked page decrypts in the browser.

    The salt is fixed per site (stored with the data), so the key a browser remembers stays valid
    across daily rebuilds; a fresh IV per build keeps every encryption unique.
    """
    iv = os.urandom(12)
    ct = AESGCM(_key(pw, salt)).encrypt(iv, html.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return json.dumps({"salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "iter": ITERATIONS})
