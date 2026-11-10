"""Admin password hashing benchmarks (PBKDF2-HMAC-SHA256, 600k iterations)."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.internal.password_hash import hash_password, is_hashed, verify_password

CASES = [
    Case(
        name="password.hash",
        group="password",
        description="hash_password(): PBKDF2-HMAC-SHA256 at 600k iterations",
        fn=lambda fixture: hash_password("benchmark-password"),
        iterations=3,
        warmup=1,
    ),
    Case(
        name="password.verify",
        group="password",
        description="verify_password(): re-derive and compare against the stored hash",
        fn=lambda fixture: verify_password("benchmark-password", fixture.password_hash),
        setup=lambda fixture: setattr(fixture, "password_hash", hash_password("benchmark-password")),
        iterations=3,
        warmup=1,
    ),
    Case(
        name="password.is_hashed",
        group="password",
        description="is_hashed(): prefix check on a stored value",
        fn=lambda fixture: is_hashed(fixture.password_hash),
        setup=lambda fixture: setattr(fixture, "password_hash", hash_password("benchmark-password")),
        iterations=100,
        warmup=10,
    ),
]
