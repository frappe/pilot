from __future__ import annotations

import json
import typing
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.jsonl_log import utc_now

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# Certbot renews from 30 days left, so a certificate under 14 days is not renewing.
RENEWAL_DAYS = 30
EXPIRING_DAYS = 14
BROKEN_STATES = frozenset({"failed", "expiring", "expired"})


@dataclass
class CertificateRecord:
    """What Pilot last saw of one certificate lineage. Read from the files on disk, never
    from the domain, so a check does not wake a sleeping host.

    `domains` are the names Pilot asks for; `covered` are the names in the file now."""

    name: str
    domains: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    expires_at: str | None = None
    checked_at: str | None = None
    error: str | None = None
    failed_at: str | None = None
    alerted: str | None = None

    @property
    def days_left(self) -> int | None:
        if self.expires_at is None:
            return None
        return (datetime.fromisoformat(self.expires_at) - datetime.now(UTC)).days

    @property
    def uncovered(self) -> list[str]:
        return [domain for domain in self.domains if domain not in self.covered]

    @property
    def state(self) -> str:
        """The certificate's state: `valid`, `pending`, `failed`, `expiring` or `expired`."""
        return self.get_domain_state(self.uncovered[0] if self.uncovered else None)

    def get_domain_state(self, domain: str | None) -> str:
        """One domain's state. A failure to add a new name does not break a name the file still covers."""
        days_left = self.days_left
        is_covered = domain is None or domain in self.covered
        if is_covered and days_left is not None and days_left < 0:
            return "expired"
        if self.error and (not is_covered or days_left is None or days_left < RENEWAL_DAYS):
            return "failed"
        if not is_covered or days_left is None:
            return "pending"
        return "expiring" if days_left < EXPIRING_DAYS else "valid"

    @property
    def is_broken(self) -> bool:
        return self.state in BROKEN_STATES

    def get_alert_payload(self, bench_name: str, site: str | None) -> dict[str, typing.Any]:
        """The feed and sink payload for a broken certificate."""
        names = ", ".join(self.domains)
        if self.state == "failed":
            verb, domain = ("issue", self.uncovered[0]) if self.uncovered else ("renew", self.domains[0])
            event, severity = "certificate_failed", "Error"
            title = f"Certificate for {domain} failed to {verb}"
            message = f"Certbot could not {verb} the certificate for {names}:\n{self.error}"
        elif self.state == "expired":
            event, severity = "certificate_expiry", "Error"
            title = f"Certificate for {self.domains[0]} has expired"
            message = f"The certificate for {names} expired and has not renewed."
        else:
            event, severity = "certificate_expiry", "Warning"
            title = f"Certificate for {self.domains[0]} is not renewing"
            message = f"The certificate for {names} expires in {self.days_left} days and has not renewed."
        return {
            "event": event,
            "title": title,
            "category": "Sites",
            "severity": severity,
            "site": site,
            "action_route": f"/sites/{site}/settings" if site else "/settings",
            "message": message,
            "context": {"bench": bench_name, "certificate": self.name, "domains": self.domains, **self.get_status()},
        }

    def get_status(self, domain: str | None = None) -> dict[str, typing.Any]:
        return {
            "state": self.get_domain_state(domain) if domain else self.state,
            "expires_at": self.expires_at,
            "days_left": self.days_left,
            "error": self.error,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class CertificateFile:
    """One certificate this bench serves: the lineage key, its domains, its file, and its site."""

    name: str
    domains: list[str]
    path: Path
    site: str | None


class BenchCertificates:
    """The bench's certificate records in logs/certificates.json, one per lineage."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self.path = bench.logs_path / "certificates.json"

    def get(self, name: str) -> CertificateRecord | None:
        return self._read().get(name)

    def record_success(self, name: str, domains: list[str], cert_file: Path) -> CertificateRecord:
        """A certificate was issued, renewed or found current: clear any failure."""
        expires_at, covered = read_certificate(cert_file)
        return self._update(name, domains, expires_at=expires_at, covered=covered, error=None, failed_at=None)

    def record_failure(self, name: str, domains: list[str], error: str) -> CertificateRecord:
        return self._update(
            name, domains, error=error.strip()[-500:] or "Certbot failed.", failed_at=utc_now()
        )

    def refresh(self, name: str, domains: list[str], cert_file: Path) -> CertificateRecord:
        """Read the expiry from the file again and keep any failure."""
        expires_at, covered = read_certificate(cert_file)
        return self._update(name, domains, expires_at=expires_at, covered=covered)

    def check(self, files: list[CertificateFile]) -> None:
        """Read each certificate file again, then alert once for each certificate that became broken."""
        for file in files:
            self.refresh(file.name, file.domains, file.path)
        self.alert_broken(files)

    def alert_broken(self, files: list[CertificateFile]) -> None:
        """Alert when a certificate enters a broken state, and forget the alert once it is valid again."""
        from pilot.core.notification.events import record_alert

        for file in files:
            record = self.get(file.name)
            if record is None:
                continue
            state = record.state if record.is_broken else None
            if state == record.alerted:
                continue
            if state:
                payload = record.get_alert_payload(self.bench.config.name, file.site)
                record_alert(self.bench, payload)
                self.bench.notifier.send(payload)
            self.mark_alerted(file.name, state)

    def mark_alerted(self, name: str, state: str | None) -> None:
        with self._open() as records:
            if name in records:
                records[name].alerted = state

    def _update(self, name: str, domains: list[str], **changes: typing.Any) -> CertificateRecord:
        with self._open() as records:
            record = records.setdefault(name, CertificateRecord(name))
            record.domains = sorted(domains)
            record.checked_at = utc_now()
            for key, value in changes.items():
                setattr(record, key, value)
            return record

    def _read(self) -> dict[str, CertificateRecord]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {name: CertificateRecord(**record) for name, record in data.items()}

    @contextmanager
    def _open(self) -> typing.Iterator[dict[str, CertificateRecord]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            records = self._read()
            yield records
            data = {name: asdict(record) for name, record in sorted(records.items())}
            replace_private_text_locked(self.path, json.dumps(data, indent=1))


def read_certificate(cert_file: Path) -> tuple[str | None, list[str]]:
    """The expiry (ISO time) and the names of a certificate file, or (None, []) when it cannot be read."""
    from pilot.managers.letsencrypt import certificate_domain_names, certificate_expiry

    expiry = certificate_expiry(cert_file)
    if expiry is None:
        return None, []
    return expiry.isoformat(), sorted(certificate_domain_names(cert_file))
