"""Reads/writes provider PATs in .bench.git.info."""

from __future__ import annotations

import json
from pathlib import Path

from pilot.utils import write_private_text

CREDENTIALS_FILENAME = ".bench.git.info"


class GitCredentialStore:
    def __init__(self, bench_root: Path) -> None:
        self.path = Path(bench_root) / CREDENTIALS_FILENAME

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def save(self, provider: str, token: str, *, username: str = "", expires_at: str | None = None) -> dict:
        existing = self.load() or {}
        record = {
            "provider": provider,
            "token": token,
            "username": username or existing.get("username", ""),
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
        write_private_text(self.path, json.dumps(record, indent=2))
