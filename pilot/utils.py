import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

from pilot.exceptions import BenchError, CommandError

if TYPE_CHECKING:
    from pilot.core.bench import BenchConfig

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


def open_private(path: Path, mode: str = "w", *, exclusive: bool = False) -> IO:
    path = Path(path)
    if mode not in {"w", "wb", "a", "ab"}:
        raise ValueError(f"Unsupported private file mode: {mode!r}")

    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if mode.startswith("a") else os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, mode)
    except Exception:
        os.close(descriptor)
        raise


def write_private_text(path: Path, content: str) -> None:
    with open_private(path) as handle:
        handle.write(content)


def make_private_directory(path: Path, *, parents: bool = False) -> None:
    path = Path(path)
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=parents, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to secure a symbolic-link directory: {path}")
    path.chmod(PRIVATE_DIRECTORY_MODE)


def admin_url(config: "BenchConfig", dev_host: str = "localhost") -> str:
    admin = config.admin
    if config.production.enabled:
        scheme = "https" if admin.tls else "http"
        return f"{scheme}://{admin.domain}"
    return f"http://{dev_host}:{admin.port}"


def cli_root() -> Path:
    import pilot as package

    return Path(package.__file__).parent.parent


def benches_dir() -> Path:
    return cli_root() / "benches"


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 100_000
    max_member_bytes: int = 8 * 1024**3
    max_total_bytes: int = 32 * 1024**3


class UnsafeArchiveError(BenchError):
    pass


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


def validate_tar_archive(path: Path, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> None:
    try:
        with tarfile.open(path) as archive:
            _validated_members(archive, limits)
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError("File is not a readable tar archive.") from exc


def extract_tar_archive(
    path: Path,
    destination: Path,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with tarfile.open(path) as archive:
            members = _validated_members(archive, limits)
            targets = [(member, _safe_target(root, member.name)) for member in members]
            for member, target in targets:
                _reject_symlink_path(root, target)
                if target.exists() and member.isdir() != target.is_dir():
                    raise UnsafeArchiveError(f"Archive member conflicts with {target}.")

            directories = [
                (member, target) for member, target in targets if member.isdir() and target != root
            ]
            for _, target in directories:
                target.mkdir(parents=True, exist_ok=True)
            for member, target in targets:
                if member.isfile():
                    _write_archive_member(archive, member, root, target)
            for member, target in reversed(directories):
                target.chmod(member.mode & 0o777)
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError(f"Archive extraction failed: {exc}") from exc


def _validated_members(
    archive: tarfile.TarFile,
    limits: ArchiveLimits,
) -> list[tarfile.TarInfo]:
    members = []
    paths: dict[tuple[str, ...], bool] = {}
    total_size = 0
    for index, member in enumerate(archive, start=1):
        if index > limits.max_members:
            raise UnsafeArchiveError("Archive exceeds the member count limit.")

        parts = _safe_parts(member.name)
        if member.issym() or member.islnk():
            raise UnsafeArchiveError(f"Archive links are not allowed: {member.name}")
        if not member.isfile() and not member.isdir():
            raise UnsafeArchiveError("Archive may contain only regular files and directories.")
        if member.size > limits.max_member_bytes:
            raise UnsafeArchiveError(f"Archive member size exceeds the limit: {member.name}")

        total_size += member.size
        if total_size > limits.max_total_bytes:
            raise UnsafeArchiveError("Archive expanded size exceeds the limit.")
        _check_path_conflict(paths, parts, member.isdir())
        paths[parts] = member.isdir()
        members.append(member)
    return members


def _safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\0" in name:
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts and name not in (".", "./"):
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    return parts


def _check_path_conflict(
    paths: dict[tuple[str, ...], bool],
    parts: tuple[str, ...],
    is_directory: bool,
) -> None:
    if parts in paths:
        raise UnsafeArchiveError(f"Archive contains a duplicate path: {'/'.join(parts)}")
    if any(paths.get(parts[:index]) is False for index in range(1, len(parts))):
        raise UnsafeArchiveError(f"Archive path crosses a regular file: {'/'.join(parts)}")
    if not is_directory and any(path[: len(parts)] == parts for path in paths):
        raise UnsafeArchiveError(f"Archive path conflicts with a directory: {'/'.join(parts)}")


def _safe_target(root: Path, name: str) -> Path:
    target = root.joinpath(*_safe_parts(name))
    _reject_symlink_path(root, target)
    target = target.resolve(strict=False)
    if not target.is_relative_to(root):
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    return target


def _reject_symlink_path(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise UnsafeArchiveError(f"Archive target crosses a symlink: {current}")


def _write_archive_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    root: Path,
    target: Path,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_path(root, target)
    if target.exists():
        target.unlink()

    source = archive.extractfile(member)
    if source is None:
        raise UnsafeArchiveError(f"Could not read archive member: {member.name}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            remaining = member.size
            while remaining:
                chunk = source.read(min(1024**2, remaining))
                if not chunk:
                    raise UnsafeArchiveError(f"Archive member is truncated: {member.name}")
                output.write(chunk)
                remaining -= len(chunk)
        target.chmod(member.mode & 0o777)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def iter_sibling_benches(bench_path: Path) -> Iterator[tuple[Path, "BenchConfig"]]:
    """Yield parse-only configs for sibling benches."""
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
            # Half-configured siblings still count for ports and hostnames.
            yield sibling, BenchConfig._from_dict(tomllib.loads(toml_path.read_text()))
        except Exception:
            continue


def normalize_host(host: str) -> str:
    """Canonical hostname for comparisons: lowercase, no trailing dot, IDNA."""
    if not host:
        return ""
    h = host.strip().lower().rstrip(".")
    with contextlib.suppress(UnicodeError, ValueError):
        h = h.encode("idna").decode("ascii")
    return h


def hosts_line_contains(
    line: str,
    hostname: str,
    address: str = "127.0.0.1",
) -> bool:
    tokens = line.split("#", 1)[0].split()
    return bool(tokens) and tokens[0] == address and hostname in tokens[1:]


def wildcard_suffix(pattern: str) -> str:
    """Return the fixed suffix of a wildcard domain pattern."""
    return pattern[1:] if pattern.startswith("*") else pattern


def matches_wildcard(domain: str, patterns: list[str]) -> bool:
    """Return whether a domain matches any wildcard pattern."""
    domain = normalize_host(domain)
    return any(domain != (suffix := wildcard_suffix(p)) and domain.endswith(suffix) for p in patterns)


def _bench_hosts(bench_dir: Path, config: "BenchConfig") -> Iterator[str]:
    """Yield every normalized hostname claimed by a bench."""
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


def host_owner(bench_path: Path, host: str) -> str | None:
    """Return the sibling bench that already claims host, if any."""
    target = normalize_host(host)
    if not target:
        return None
    for sibling, config in iter_sibling_benches(bench_path):
        if target in _bench_hosts(sibling, config):
            return config.name
    return None


def installed_app_version(env_path: Path, name: str) -> str:
    """Version of an installed app read from its dist-info METADATA - no subprocess."""
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
    raise BenchError("yarn not found - run bench init to install it.")


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
    tee_output: bool = False,
) -> subprocess.CompletedProcess:
    if tee_output:
        return _run_command_tee(argv, cwd, env)
    process = _start_process(argv, cwd, env, stream_output)
    stdout, stderr = _wait_for_process(process, argv, timeout)
    _raise_on_failure(argv, process, stderr, stream_output, redactions)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _run_command_tee(
    argv: list[str], cwd: Path | None, env: dict | None
) -> subprocess.CompletedProcess:
    """Stream combined output live while capturing it for later classification."""
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=_inherit_task_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    captured: list[str] = []
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            captured.append(line)
    except KeyboardInterrupt:
        _terminate_process_group(process)
        raise
    process.wait()
    return subprocess.CompletedProcess(argv, process.returncode, "".join(captured), "")


def _inherit_task_env(env: dict | None) -> dict | None:
    inherited = {
        key: os.environ[key]
        for key in ("BENCH_TASK_LAUNCH_ID", "PILOT_NONINTERACTIVE_PRIVILEGES")
        if key in os.environ
    }
    if env is not None and inherited:
        return {**env, **inherited}
    return env


def _start_process(
    argv: list[str], cwd: Path | None, env: dict | None, stream_output: bool
) -> subprocess.Popen:
    detach_session = not (argv and (argv[0] == "sudo" or argv[0].endswith("/sudo")))
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=_inherit_task_env(env),
        stdout=None if stream_output else subprocess.PIPE,
        stderr=None if stream_output else subprocess.PIPE,
        start_new_session=detach_session,
    )


def _wait_for_process(process: subprocess.Popen, argv: list[str], timeout: float | None):
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise CommandError(
            f"Command {argv[0]!r} timed out after {timeout}s and was terminated.", returncode=-1
        ) from exc
    except KeyboardInterrupt:
        _terminate_process_group(process)
        raise


def _terminate_process_group(process: subprocess.Popen) -> None:
    # The child leads its own session, so killing its pgid reaches descendants too.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _raise_on_failure(argv, process, stderr, stream_output, redactions) -> None:
    if process.returncode == 0:
        return
    stderr_text = redact_text(stderr.decode(), redactions) if not stream_output and stderr else ""
    raise CommandError(
        f"Command {argv[0]!r} failed with exit code {process.returncode}.\n{stderr_text}".strip(),
        returncode=process.returncode,
    )
