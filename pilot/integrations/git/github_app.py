from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from pilot.integrations.central.client import CentralClient, CentralClientError
from pilot.integrations.git.credentials import GITHUB_APP_AUTH_TYPE, GitCredentialStore

# A token this close to expiry could expire during a long clone.
_EXPIRY_MARGIN = timedelta(minutes=10)


class GitHubAppTokens:
    """Per-process cache of the read-only repository tokens Central mints."""

    _tokens: ClassVar[dict[str, tuple[str, datetime]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get(cls, full_name: str) -> str:
        """A token for ``owner/name``, or "" when Central grants no access to it."""
        key = full_name.lower()
        with cls._lock:
            token, expires_at = cls._tokens.get(key, ("", datetime.min.replace(tzinfo=UTC)))
        if expires_at > datetime.now(UTC) + _EXPIRY_MARGIN:
            return token

        try:
            minted = CentralClient().get_github_repository_token(full_name)
        except CentralClientError:
            # Not granted, or Central is down: a public repository still works without a token.
            return ""

        expires_at = datetime.fromisoformat(minted["expires_at"])
        with cls._lock:
            cls._tokens[key] = (minted["token"], expires_at)
        return minted["token"]

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._tokens.clear()


class GitHubAppGrant:
    """This host's GitHub App grant. Central owns it; the store only marks it in place of a token."""

    def __init__(self, store: GitCredentialStore) -> None:
        self.store = store
        self.client = CentralClient()

    @property
    def is_active(self) -> bool:
        return (self.store.load() or {}).get("auth_type") == GITHUB_APP_AUTH_TYPE

    def get_status(self) -> dict:
        """Central's view of the grant. The store follows it."""
        status = self.client.get_github_status()
        accounts = ", ".join(connection["account"] for connection in status["connections"])
        if accounts and not (self.is_active and (self.store.load() or {}).get("username") == accounts):
            self.store.save_github_app(accounts)
        elif not accounts and self.is_active:
            self._forget()
        return status

    def disconnect(self) -> None:
        self.client.disconnect_github()
        self._forget()

    def _forget(self) -> None:
        self.store.clear()
        GitHubAppTokens.clear()
