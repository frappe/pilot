"""Manage the bench user's authorized_keys file."""

from __future__ import annotations

import base64
import builtins
import contextlib
import fcntl
import hashlib
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pilot.exceptions import BenchError

_KEY_TYPE_PREFIXES = ("ssh-", "ecdsa-sha2-", "sk-ssh-", "sk-ecdsa-sha2-")


class SSHKeyError(BenchError):
    """A key was malformed, duplicated, missing, or the last one remaining."""


class InvalidSSHKeyError(SSHKeyError):
    pass


class SSHKeyAlreadyExistsError(SSHKeyError):
    pass


class SSHKeyNotFoundError(SSHKeyError):
    pass


class LastSSHKeyError(SSHKeyError):
    pass


@dataclass
class SSHKey:
    key_type: str
    fingerprint: str
    comment: str


def _is_key_type(token: str) -> bool:
    return token.startswith(_KEY_TYPE_PREFIXES)


def _fingerprint(blob: str) -> str:
    digest = hashlib.sha256(base64.b64decode(blob)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _parse_line(line: str) -> tuple[str, str, str] | None:
    """Parse one authorized_keys line, ignoring options and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    for index, token in enumerate(parts):
        if _is_key_type(token) and index + 1 < len(parts):
            return token, parts[index + 1], " ".join(parts[index + 2 :])
    return None


def _validate(public_key: str) -> tuple[str, str, str]:
    """Validate key blob and declared algorithm."""
    parsed = _parse_line(public_key)
    if parsed is None:
        raise InvalidSSHKeyError("Not a valid SSH public key.")
    key_type, blob, comment = parsed
    try:
        raw = base64.b64decode(blob, validate=True)
        length = struct.unpack(">I", raw[:4])[0]
        algorithm = raw[4 : 4 + length].decode()
    except (ValueError, struct.error, UnicodeDecodeError) as exc:
        raise InvalidSSHKeyError("The public key is malformed.") from exc
    if algorithm != key_type:
        raise InvalidSSHKeyError("The key type does not match the key data.")
    return key_type, blob, comment


class AuthorizedKeysStore:
    """Reads/writes ``authorized_keys`` under an exclusive advisory lock."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.home() / ".ssh" / "authorized_keys")

    # Other methods below spell the builtin as builtins.list[...] - this method's
    # own name would otherwise shadow it in their annotations.
    def list(self) -> builtins.list[SSHKey]:
        return [self._to_key(parsed) for parsed in self._parse_lines(self._read())]

    def add(self, public_key: str) -> SSHKey:
        key_type, blob, comment = _validate(public_key)
        fingerprint = _fingerprint(blob)
        with self._locked():
            lines = self._read()
            if any(_fingerprint(p[1]) == fingerprint for p in self._parse_lines(lines)):
                raise SSHKeyAlreadyExistsError("That key is already authorized.")
            line = " ".join(part for part in (key_type, blob, comment) if part)
            self._write([*lines, line])
        return SSHKey(key_type=key_type, fingerprint=fingerprint, comment=comment)

    def remove(self, fingerprint: str) -> None:
        with self._locked():
            lines = self._read()
            keys = self._parse_lines(lines)
            if not any(_fingerprint(blob) == fingerprint for _, blob, _ in keys):
                raise SSHKeyNotFoundError("No authorized key matches that fingerprint.")
            if len(keys) <= 1:
                raise LastSSHKeyError("Refusing to remove the last authorized key.")
            kept = [line for line in lines if self._line_fingerprint(line) != fingerprint]
            self._write(kept)

    def _to_key(self, parsed: tuple[str, str, str]) -> SSHKey:
        key_type, blob, comment = parsed
        return SSHKey(key_type=key_type, fingerprint=_fingerprint(blob), comment=comment)

    def _parse_lines(self, lines: builtins.list[str]) -> builtins.list[tuple[str, str, str]]:
        return [parsed for line in lines if (parsed := _parse_line(line))]

    def _line_fingerprint(self, line: str) -> str | None:
        parsed = _parse_line(line)
        return _fingerprint(parsed[1]) if parsed else None

    def _read(self) -> builtins.list[str]:
        try:
            return self.path.read_text().splitlines()
        except FileNotFoundError:
            return []

    def _write(self, lines: builtins.list[str]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = "\n".join(lines) + "\n" if lines else ""
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".authorized_keys-")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @contextlib.contextmanager
    def _locked(self):
        # Lock the .ssh directory, not the file: an atomic write replaces the
        # file's inode, so a lock on it wouldn't serialize the next writer.
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
