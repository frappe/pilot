"""One-way hashing for the Admin password, in the shape htpasswd files use.

Format: ``$pbkdf2-sha256$<iterations>$<salt>$<derived key>``, both values base64.
PBKDF2-HMAC-SHA256 comes from hashlib, so this adds no dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2-sha256"
ITERATIONS = 600_000  # OWASP's 2023 floor for PBKDF2-HMAC-SHA256
_SALT_BYTES = 16
_PREFIX = f"${ALGORITHM}$"


def hash_password(password: str) -> str:
    """Hash a password for storage in bench.toml."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = _derive(password, salt, ITERATIONS)
    return f"{_PREFIX}{ITERATIONS}${_b64(salt)}${_b64(derived)}"


def is_hashed(stored: str) -> bool:
    """Whether this stored value is already a hash rather than a cleartext password."""
    return bool(stored) and stored.startswith(_PREFIX)


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash.

    A bench that has not run the `hash_admin_password` patch still holds cleartext, so
    that is compared directly - the patch, not this function, is what migrates it.
    """
    if not password or not stored:
        return False
    if not is_hashed(stored):
        return hmac.compare_digest(password, stored)
    try:
        _, _, iterations, salt, derived = stored.split("$")
        expected = _unb64(derived)
        actual = _derive(password, _unb64(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    """The PBKDF2-SHA256 key for the password."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


def _b64(raw: bytes) -> str:
    """Standard base64 text."""
    return base64.b64encode(raw).decode()


def _unb64(value: str) -> bytes:
    """Bytes from strict base64 text."""
    return base64.b64decode(value, validate=True)
