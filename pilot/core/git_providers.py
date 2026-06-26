"""Provider-agnostic Git integration for cloning private app repositories.

The flow is deliberately split into two layers:

* the **API layer** (a Personal Access Token) is used for administrative tasks
  the user does through the UI — validating the connection and listing repos;
* the **transport layer** is, for now, token-embedded HTTPS clone URLs.

The token is persisted (unencrypted, by request) in a ``.bench.git.info`` file
at the bench root. Only GitHub is fully implemented; ``GitLabProvider`` is a
stub that maps out the same surface for a later phase.
"""

from __future__ import annotations

import abc
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CREDENTIALS_FILENAME = ".bench.git.info"

# Per-provider Fine-Grained PAT generation links, pre-scoped where the provider
# supports it, surfaced in the UI so the user lands on the right settings page.
# Classic PAT URL pre-fills the `repo` scope so the user only has to click
# "Generate token" — no manual scope selection needed.
TOKEN_HELP_URLS = {
    "github": "https://github.com/settings/tokens/new?scopes=repo&description=Bench+CLI",
    "gitlab": "https://gitlab.com/-/user_settings/personal_access_tokens",
}


class GitAuthError(Exception):
    """The provider API rejected the token (HTTP 401/403)."""


class GitProviderError(Exception):
    """A provider API call failed for a non-auth reason."""


# ── Credential store ──────────────────────────────────────────────────────────


class GitCredentialStore:
    """Reads/writes the ``.bench.git.info`` file holding the PAT and metadata.

    Stored as plain JSON (encryption intentionally omitted for now). The file is
    written with ``0600`` permissions so it is at least not world-readable.
    """

    def __init__(self, bench_root: Path) -> None:
        self.path = Path(bench_root) / CREDENTIALS_FILENAME

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def save(self, provider: str, token: str, *, expires_at: str | None = None) -> dict:
        existing = self.load() or {}
        record = {
            "provider": provider,
            "token": token,
            "token_expires_at": expires_at or existing.get("token_expires_at"),
            "is_token_valid": True,
        }
        self._write(record)
        return record

    def mark_invalid(self) -> None:
        record = self.load()
        if record and record.get("is_token_valid"):
            record["is_token_valid"] = False
            self._write(record)

    def mark_valid(self) -> None:
        record = self.load()
        if record and not record.get("is_token_valid"):
            record["is_token_valid"] = True
            self._write(record)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def _write(self, record: dict) -> None:
        self.path.write_text(json.dumps(record, indent=2))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


# ── Providers ─────────────────────────────────────────────────────────────────


class GitProvider(abc.ABC):
    """Base class for a Git hosting provider's administrative API."""

    name: str = ""
    host: str = ""

    def __init__(self, token: str = "") -> None:
        self.token = token

    @abc.abstractmethod
    def validate(self) -> dict:
        """Ping the provider's identity endpoint; return account info.

        Raises ``GitAuthError`` if the token is rejected.
        """

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
        """Return the raw text content of *path* at *ref* in *repo_url*.

        Raises ``GitProviderError`` if the file is not found or the provider
        does not support this operation.
        """
        raise GitProviderError(f"Fetching repository files is not supported for {self.name}.")

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


class GitHubProvider(GitProvider):
    name = "github"
    host = "github.com"
    api_base = "https://api.github.com"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bench-cli",
        }

    def validate(self) -> dict:
        data, _ = self._get_json(f"{self.api_base}/user", self._headers())
        return {"login": data.get("login"), "name": data.get("name")}

    def list_repos(self) -> list[dict]:
        repos: list[dict] = []
        # A couple of pages of the most recently pushed repos is plenty for a
        # picker; the user can always paste a URL for anything older.
        for page in range(1, 4):
            url = (
                f"{self.api_base}/user/repos"
                f"?per_page=100&page={page}&sort=pushed&affiliation=owner,collaborator,organization_member"
            )
            batch, _ = self._get_json(url, self._headers())
            if not batch:
                break
            for r in batch:
                repos.append({
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "private": r.get("private", False),
                    "description": r.get("description") or "",
                    "default_branch": r.get("default_branch") or "",
                    "clone_url": r.get("clone_url") or "",
                })
            if len(batch) < 100:
                break
        return repos

    def authenticated_clone_url(self, repo_url: str) -> str:
        return inject_https_token(repo_url, "x-access-token", self.token)

    def list_branches(self, full_name: str) -> list[str]:
        url = f"{self.api_base}/repos/{full_name}/branches?per_page=100"
        data, _ = self._get_json(url, self._headers())
        return [b["name"] for b in data]

    def fetch_raw_file(self, repo_url: str, path: str, ref: str = "HEAD") -> str:
        owner, repo = _parse_github_owner_repo(repo_url)
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        headers = {"User-Agent": "bench"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GitAuthError(f"Access denied reading {path} from repository.") from exc
            if exc.code == 404:
                raise GitProviderError(f"{path} not found in repository.") from exc
            raise GitProviderError(f"HTTP {exc.code} reading {path}.") from exc
        except urllib.error.URLError as exc:
            raise GitProviderError(f"Could not reach GitHub: {exc.reason}.") from exc


class GitLabProvider(GitProvider):
    """Stub — mapped out for a later phase, not wired up yet."""

    name = "gitlab"
    host = "gitlab.com"

    def validate(self) -> dict:
        raise GitProviderError("GitLab support is not implemented yet.")

    def list_repos(self) -> list[dict]:
        raise GitProviderError("GitLab support is not implemented yet.")

    def authenticated_clone_url(self, repo_url: str) -> str:
        return inject_https_token(repo_url, "oauth2", self.token)

    def list_branches(self, full_name: str) -> list[str]:
        raise GitProviderError("GitLab support is not implemented yet.")


PROVIDERS: dict[str, type[GitProvider]] = {
    GitHubProvider.name: GitHubProvider,
    GitLabProvider.name: GitLabProvider,
}


def provider_for_name(name: str, token: str = "") -> GitProvider:
    cls = PROVIDERS.get((name or "").lower())
    if cls is None:
        raise GitProviderError(f"Unknown git provider: {name!r}.")
    return cls(token)


def provider_for_repo(repo_url: str, token: str = "") -> GitProvider | None:
    for cls in PROVIDERS.values():
        if cls.host and cls.host in (repo_url or ""):
            return cls(token)
    return None


# ── URL helpers ───────────────────────────────────────────────────────────────


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
    """Embed ``username:token`` userinfo into an https clone URL.

    Falls back to the normalized URL untouched when there is no token.
    """
    https = normalize_to_https(repo_url)
    if not token or not https.startswith("https://"):
        return https
    rest = https[len("https://"):]
    # Strip any userinfo already present so we don't double up.
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    quoted = urllib.parse.quote(token, safe="")
    return f"https://{username}:{quoted}@{rest}"


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS URL.

    Accepts ``https://github.com/owner/repo`` and ``…/repo.git``.
    Raises ``GitProviderError`` when the URL cannot be parsed.
    """
    url = normalize_to_https(repo_url).rstrip("/").removesuffix(".git")
    parts = url.split("/")
    # Expect ['https:', '', 'github.com', 'owner', 'repo']
    if len(parts) < 5 or not parts[-2] or not parts[-1]:
        raise GitProviderError(f"Cannot parse owner/repo from URL: {repo_url!r}")
    return parts[-2], parts[-1]


def resolve_app_name_from_repo(bench_root: Path, repo_url: str, branch: str = "") -> str:
    """Fetch *pyproject.toml* from *repo_url* and return ``project.name``.

    Uses the stored credential when available so private repos work.
    Public repos are fetched anonymously when no token is on file.
    """
    import tomllib

    record = GitCredentialStore(bench_root).load()
    token = record.get("token", "") if record else ""

    provider = provider_for_repo(repo_url, token)
    if provider is None:
        raise GitProviderError(
            f"No supported git provider found for {repo_url!r}. "
            "Only GitHub is supported for automatic app-name resolution."
        )

    ref = branch.strip() or "HEAD"
    content = provider.fetch_raw_file(repo_url, "pyproject.toml", ref)

    try:
        data = tomllib.loads(content)
    except Exception as exc:
        raise GitProviderError(f"Could not parse pyproject.toml: {exc}") from exc

    name = (data.get("project") or {}).get("name", "").strip()
    if not name:
        raise GitProviderError(
            "pyproject.toml does not contain a [project] name field."
        )
    return name


def authenticated_url_for(bench_root: Path, repo_url: str) -> str:
    """Return a clone URL for ``repo_url``, token-embedded when applicable.

    Consults the bench's stored credential. If the repo's host matches the
    connected provider and a token is on file, the token is embedded; otherwise
    the original URL is returned unchanged (public repos clone fine without it).
    """
    record = GitCredentialStore(bench_root).load()
    if not record or not record.get("token"):
        return repo_url
    provider = provider_for_repo(repo_url, record["token"])
    if provider is None or provider.name != record.get("provider"):
        return repo_url
    return provider.authenticated_clone_url(repo_url)
