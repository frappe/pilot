"""Provider API contracts and URL helpers for private Git repositories."""

from __future__ import annotations

import abc
import json
import urllib.error
import urllib.parse
import urllib.request

from pilot.exceptions import BenchError

# Provider token-generation links surfaced in the UI.
TOKEN_HELP_URLS = {
    "github": "https://github.com/settings/tokens/new?scopes=repo&description=Bench+CLI",
}


class GitAuthError(BenchError):
    """The provider API rejected the token (HTTP 401/403)."""


class GitProviderError(BenchError):
    """A provider API call failed for a non-auth reason."""


class GitProvider(abc.ABC):
    """Base class for a Git hosting provider's administrative API."""

    name: str = ""
    host: str = ""

    def __init__(self, token: str = "") -> None:
        self.token = token

    @abc.abstractmethod
    def validate(self) -> dict:
        """Ping the identity endpoint and return account info."""

    @abc.abstractmethod
    def list_repos(self) -> list[dict]:
        """Return repositories the token can access (private and public)."""

    @abc.abstractmethod
    def authenticated_clone_url(self, repo_url: str) -> str:
        """Rewrite ``repo_url`` into a token-embedded HTTPS clone URL."""

    @abc.abstractmethod
    def list_branches(self, full_name: str) -> list[str]:
        """Return branch names for *full_name* (``owner/repo``), up to 100."""

    def fetch_raw_file(self, repo_url: str, path: str, ref: str = "HEAD") -> str:
        """Return raw text content from a repository ref."""
        raise GitProviderError(f"Fetching repository files is not supported for {self.name}.")

    def get_default_branch(self, full_name: str) -> str:
        """Return the repository's default branch name, or "" if unknown."""
        return ""

    # -- shared helpers --------------------------------------------------------

    def owns(self, repo_url: str) -> bool:
        return self.host in (repo_url or "")

    def _get_json(self, url: str, headers: dict):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
                return payload, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GitAuthError(f"{self.name} rejected the token (HTTP {exc.code}).") from exc
            raise GitProviderError(f"{self.name} API error (HTTP {exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise GitProviderError(f"Could not reach {self.name}: {exc.reason}.") from exc


def normalize_to_https(repo_url: str) -> str:
    """Normalize a git remote (scp-style or https) to a plain https URL."""
    url = (repo_url or "").strip()
    # scp-style: git@github.com:owner/repo(.git)
    if url.startswith("git@"):
        host, _, path = url[len("git@"):].partition(":")
        return f"https://{host}/{path}"
    if url.startswith("ssh://"):
        parsed = urllib.parse.urlparse(url)
        return f"https://{parsed.hostname}{parsed.path}"
    return url


def inject_https_token(repo_url: str, username: str, token: str) -> str:
    """Embed username/token userinfo into an HTTPS clone URL."""
    https = normalize_to_https(repo_url)
    if not token or not https.startswith("https://"):
        return https
    rest = https[len("https://"):]
    # Strip any userinfo already present so we don't double up.
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    quoted = urllib.parse.quote(token, safe="")
    return f"https://{username}:{quoted}@{rest}"
