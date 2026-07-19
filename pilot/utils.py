import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pilot.exceptions import BenchError, CommandError

if TYPE_CHECKING:
    from pilot.core.bench import BenchConfig


def iter_sibling_benches(bench_path: Path) -> Iterator[tuple[Path, "BenchConfig"]]:
    """Yield ``(bench_dir, parsed bench.toml)`` for every *other* bench that
    shares this bench's parent ``benches/`` directory.

    Parse-only (no validation) so half-configured benches are still seen.
    Skips ``bench_path`` itself and any directory without a readable
    ``bench.toml``. ``bench_path`` need not exist yet (e.g. during ``bench new``).
    """
    import tomllib

    from pilot.core.bench import BenchConfig

    parent = bench_path.parent
    if not parent.is_dir():
        return
    me = bench_path.resolve()
    for sibling in parent.iterdir():
        if not sibling.is_dir() or sibling.resolve() == me:
            continue
        toml_path = sibling / "bench.toml"
        if not toml_path.exists():
            continue
        try:
            # Parse-only (no validate) so half-configured siblings are still
            # seen — important for port-offset collision avoidance and
            # cross-bench hostname checks.
            yield sibling, BenchConfig._from_dict(tomllib.loads(toml_path.read_text()))
        except Exception:
            continue


def normalize_host(host: str) -> str:
    """Canonical form for hostname comparison: lowercased, trailing dot stripped,
    internationalized labels reduced to their ASCII (IDNA) form. Returns an empty
    string for falsy input. Best-effort — a name that cannot be IDNA-encoded is
    returned lowercased/stripped so comparison still works for ASCII domains."""
    if not host:
        return ""
    h = host.strip().lower().rstrip(".")
    try:
        h = h.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        pass
    return h


def hosts_line_contains(
    line: str,
    hostname: str,
    address: str = "127.0.0.1",
) -> bool:
    tokens = line.split("#", 1)[0].split()
    return bool(tokens) and tokens[0] == address and hostname in tokens[1:]


def wildcard_suffix(pattern: str) -> str:
    """The fixed part of a wildcard domain pattern, e.g. '*.example.com' -> '.example.com',
    '*-box1.example.com' -> '-box1.example.com'."""
    return pattern[1:] if pattern.startswith("*") else pattern


def matches_wildcard(domain: str, patterns: list[str]) -> bool:
    """Whether ``domain`` ends with the fixed part of one of the wildcard ``patterns``
    and has something before it (a bare suffix with no label doesn't match)."""
    domain = normalize_host(domain)
    return any(domain != (suffix := wildcard_suffix(p)) and domain.endswith(suffix) for p in patterns)


def _bench_hosts(bench_dir: Path, config: "BenchConfig") -> Iterator[str]:
    """Yield every hostname a bench claims: its admin domain, each site's name,
    and each site's configured ``domains`` aliases — all normalized."""
    import json

    if config.admin.domain:
        yield normalize_host(config.admin.domain)
    sites_dir = bench_dir / "sites"
    if not sites_dir.is_dir():
        return
    for site in sites_dir.iterdir():
        cfg = site / "site_config.json"
        if not cfg.exists():
            continue
        yield normalize_host(site.name)
        try:
            for alias in json.loads(cfg.read_text()).get("domains", []) or []:
                name = alias.get("domain") if isinstance(alias, dict) else alias
                if name:
                    yield normalize_host(str(name))
        except Exception:
            continue


def host_owner(bench_path: Path, host: str) -> Optional[str]:
    """Return the name of *another* bench that already claims ``host`` — as one of
    its sites (name or alias) or as its ``admin.domain`` — or ``None`` if the host
    is free across all sibling benches.

    Hosts are compared in normalized form (lowercase, no trailing dot, IDNA), so
    two benches can never fight over the same hostname served by the same nginx.
    """
    target = normalize_host(host)
    if not target:
        return None
    for sibling, config in iter_sibling_benches(bench_path):
        if target in _bench_hosts(sibling, config):
            return config.name
    return None


def installed_app_version(env_path: Path, name: str) -> str:
    """Version of an installed app read from its dist-info METADATA — no subprocess."""
    lib_dir = env_path / "lib"
    if not lib_dir.is_dir():
        return ""
    normalized = name.replace("-", "_")
    for python_dir in lib_dir.iterdir():
        site_packages = python_dir / "site-packages"
        if not site_packages.is_dir():
            continue
        for dist_info in site_packages.glob(f"{normalized}-*.dist-info"):
            metadata = dist_info / "METADATA"
            if not metadata.exists():
                continue
            for line in metadata.read_text(errors="replace").splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    return ""


def git_has_local_changes(path: Path) -> bool:
    """True if the repo at *path* has uncommitted edits or commits not yet on upstream."""
    from pilot.internal.git import GitRepo

    return GitRepo(path).has_local_changes


def get_yarn_bin() -> str:
    if yarn := shutil.which("yarn"):
        return yarn
    local_yarn = Path.home() / ".local" / "bin" / "yarn"
    if local_yarn.exists():
        return str(local_yarn)
    raise BenchError("yarn not found — run bench init to install it.")


def redact_text(text: str, secrets: list[str] | None) -> str:
    if not text or not secrets:
        return text
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def run_command(
    argv: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    stream_output: bool = False,
    timeout: float | None = None,
    redactions: list[str] | None = None,
) -> subprocess.CompletedProcess:
    process = _start_process(argv, cwd, env, stream_output)
    stdout, stderr = _wait_for_process(process, argv, timeout)
    _raise_on_failure(argv, process, stderr, stream_output, redactions)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _start_process(argv: list[str], cwd: Path | None, env: dict | None, stream_output: bool) -> subprocess.Popen:
    run_env = dict(env) if env is not None else dict(os.environ)
    inherited = {
        key: os.environ[key]
        for key in ("BENCH_TASK_LAUNCH_ID", "PILOT_NONINTERACTIVE_PRIVILEGES")
        if key in os.environ
    }
    run_env.update(inherited)

    # Automatically prepend the latest NVM Node version to PATH if present
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        versions = []
        for p in nvm_dir.iterdir():
            if p.is_dir() and p.name.startswith("v"):
                try:
                    parts = [int(x) for x in p.name[1:].split(".")]
                    versions.append((parts, p / "bin"))
                except ValueError:
                    continue
        if versions:
            versions.sort(reverse=True)
            node_bin_dir = str(versions[0][1])
            current_path = run_env.get("PATH", "")
            if node_bin_dir not in current_path:
                run_env["PATH"] = os.pathsep.join([node_bin_dir, current_path]) if current_path else node_bin_dir

    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=run_env,
        stdout=None if stream_output else subprocess.PIPE,
        stderr=None if stream_output else subprocess.PIPE,
        start_new_session=True,
    )


def _wait_for_process(process: subprocess.Popen, argv: list[str], timeout: float | None):
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise CommandError(f"Command {argv[0]!r} timed out after {timeout}s and was terminated.", returncode=-1)
    except KeyboardInterrupt:
        _terminate_process_group(process)
        raise


def _terminate_process_group(process: subprocess.Popen) -> None:
    # The child leads its own session, so killing its pgid reaches descendants too.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _raise_on_failure(argv, process, stderr, stream_output, redactions) -> None:
    if process.returncode == 0:
        return
    stderr_text = redact_text(stderr.decode(), redactions) if not stream_output and stderr else ""
    raise CommandError(
        f"Command {argv[0]!r} failed with exit code {process.returncode}.\n{stderr_text}".strip(),
        returncode=process.returncode,
    )


def _get_machine_id() -> bytes:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, "rb") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            continue
            
    # Generate and persist a secure fallback key instead of using a static string
    fallback_path = Path.home() / ".config" / "pilot" / ".secret_key"
    try:
        if fallback_path.exists():
            return fallback_path.read_bytes()
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        fallback_path.write_bytes(key)
        fallback_path.chmod(0o600)
        return key
    except Exception:
        # Absolute last resort fallback
        return b"pilot-fallback-secret-key-salt"


def encrypt(plain_text: str) -> str:
    import base64
    import hashlib
    import os

    if not plain_text:
        return ""

    machine_id = _get_machine_id()
    salt = os.urandom(16)
    iterations = 600000
    data_bytes = plain_text.encode("utf-8")
    keystream = hashlib.pbkdf2_hmac("sha256", machine_id, salt, iterations, dklen=len(data_bytes))
    cipher_bytes = bytes(a ^ b for a, b in zip(data_bytes, keystream))
    
    # Store iterations in the ciphertext payload for future-proofing
    return f"{salt.hex()}:{iterations}:{base64.b64encode(cipher_bytes).decode('utf-8')}"


def decrypt(cipher_text: str) -> str:
    import base64
    import hashlib

    if not cipher_text:
        return ""
    if ":" not in cipher_text:
        return cipher_text
        
    try:
        parts = cipher_text.split(":")
        if len(parts) == 2:
            salt_hex, b64_cipher = parts
            iterations = 1000  # Legacy tokens
        elif len(parts) == 3:
            salt_hex, iter_str, b64_cipher = parts
            iterations = int(iter_str)
        else:
            return cipher_text

        salt = bytes.fromhex(salt_hex)
        cipher_bytes = base64.b64decode(b64_cipher)
        machine_id = _get_machine_id()

        keystream = hashlib.pbkdf2_hmac("sha256", machine_id, salt, iterations, dklen=len(cipher_bytes))
        plain_bytes = bytes(a ^ b for a, b in zip(cipher_bytes, keystream))
        return plain_bytes.decode("utf-8")
    except Exception:
        return cipher_text

