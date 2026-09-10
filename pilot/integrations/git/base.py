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
    "github": "https://github.com/settings/tokens/new?scopes=repo&description=Pilot",
}


class GitAuthError(BenchError):
    """The provider API rejected the token (HTTP 401/403)."""


class GitProviderError(BenchError):
    """A provider API call failed for a non-auth reason."""


class BranchNotFoundError(GitProviderError):
    """The repository has no branch by that name."""


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
    def auth_config(self, repo_url: str) -> dict[str, str]:
        """Git config that authenticates requests to ``repo_url``."""

    @abc.abstractmethod
    def list_branches(self, full_name: str) -> list[str]:
        """Return branch names for *full_name* (``owner/repo``)."""

    def has_branch(self, full_name: str, branch: str) -> bool:
        """Whether *branch* exists, asked of the provider rather than of the
        branch list - a repository can have more branches than a page holds."""
        return branch in self.list_branches(full_name)

    def fetch_raw_file(self, repo_url: str, path: str, ref: str = "HEAD") -> str:
        """Return raw text content from a repository ref."""
        raise GitProviderError(f"Fetching repository files is not supported for {self.name}.")

    def get_default_branch(self, full_name: str) -> str:
        """Return the repository's default branch name, or "" if unknown."""
        return ""

    # -- shared helpers --------------------------------------------------------

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


def without_credentials(repo_url: str) -> str:
    """The same URL with any user:token userinfo dropped."""
    parsed = urllib.parse.urlsplit(repo_url or "")
    if not parsed.username and not parsed.password:
        return repo_url
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit(parsed._replace(netloc=netloc))


def repo_host(repo_url: str) -> str:
    """The repo URL's hostname, lowercased; "" when it has none (a local path)."""
    return (urllib.parse.urlsplit(normalize_to_https(repo_url)).hostname or "").lower()


def normalize_to_https(repo_url: str) -> str:
    """Normalize a git remote (scp-style or https) to a plain https URL."""
    url = (repo_url or "").strip()
    # scp-style: git@github.com:owner/repo(.git)
    if url.startswith("git@"):
        host, _, path = url[len("git@") :].partition(":")
        return f"https://{host}/{path}"
    if url.startswith("ssh://"):
        parsed = urllib.parse.urlparse(url)
        return f"https://{parsed.hostname}{parsed.path}"
    return url


def basic_auth_config(repo_url: str, username: str, token: str) -> dict[str, str]:
    """An Authorization header for this repo's host, as git config.

    A header keeps the token out of argv and out of .git/config, unlike userinfo in the
    clone URL. Scoped to the host so it never travels to another one."""
    import base64

    parsed = urllib.parse.urlsplit(normalize_to_https(repo_url))
    if not token or parsed.scheme != "https" or not parsed.hostname:
        return {}
    credential = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {f"http.https://{parsed.hostname}/.extraHeader": f"Authorization: Basic {credential}"}


def same_repository(url_a: str, url_b: str) -> bool:
    """Whether two repo URLs name the same repository, ignoring credentials,
    scp-vs-https style, and a trailing ``.git``."""
    key_a = _repository_key(url_a)
    return bool(key_a) and key_a == _repository_key(url_b)


def _repository_key(repo_url: str) -> str:
    parsed = urllib.parse.urlsplit(normalize_to_https(repo_url))
    if not parsed.hostname:
        return ""
    path = parsed.path.removesuffix(".git").strip("/")
    return f"{parsed.hostname}/{path}".lower()
