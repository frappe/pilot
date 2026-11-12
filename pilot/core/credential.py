from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta


def get_jwt_expiry(token: str) -> datetime | None:
    """The `exp` claim of a JWT, unverified. None when the token has no readable one."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(claims["exp"], UTC)
    except (IndexError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


class RotatingCredential:
    """A token that expires and is fetched again from its issuer before it dies."""

    rotate_before_seconds = 24 * 60 * 60

    def __init__(self, token: str) -> None:
        self.token = token

    @property
    def expires_at(self) -> datetime | None:
        """When the token expires, if known."""
        return get_jwt_expiry(self.token)

    def is_expiring(self, now: datetime | None = None) -> bool:
        """True inside the rotation window, past expiry, or when the expiry is unknown."""
        if self.expires_at is None:
            return True

        now = now or datetime.now(UTC)
        return self.expires_at - now <= timedelta(seconds=self.rotate_before_seconds)

    def rotate_if_expiring(self, now: datetime | None = None) -> bool:
        """Replace the token when it is expiring. True when the token changed."""
        if not self.is_expiring(now):
            return False

        previous, self.token = self.token, self.rotate()
        return self.token != previous

    def rotate(self) -> str:
        """Fetch and persist a new token, returning it. Keep the old one on failure."""
        raise NotImplementedError
