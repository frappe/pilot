from __future__ import annotations

import hmac
import json
import secrets
import time

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import TwoFactorError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

# One step either side of now, so a phone whose clock drifts slightly still works.
DRIFT_STEPS = 1
# An enrollment nobody completes is dropped, so an abandoned secret cannot linger.
PENDING_TTL = 24 * 3600
# Let's give 10 recovery codes to bypass the second factor, if exhausted users will have to regenerate them.
RECOVERY_CODE_COUNT = 10
# Each device holds its own secret, so this also caps how many people can sign in
# during the same 30-second window.
MAX_ENROLLED_DEVICES = 10
# The name is the key and travels in the URL path.
MAX_DEVICE_NAME_LENGTH = 40


def normalise_device_name(name: str) -> str:
    """Collapse whitespace so "My  Phone" and "My Phone" cannot both be enrolled."""
    return " ".join(name.split())


def validate_device_name(name: str) -> str:
    """Return the stored form of a device name, or explain why it cannot be used."""
    name = normalise_device_name(name)
    if not name:
        raise TwoFactorError("A device name is required.")
    if len(name) > MAX_DEVICE_NAME_LENGTH:
        raise TwoFactorError(f"Device names are limited to {MAX_DEVICE_NAME_LENGTH} characters.")
    # Werkzeug decodes the path segment, so any printable character survives the round
    # trip. A slash does not: proxies decode %2F back into a separator before routing.
    if "/" in name:
        raise TwoFactorError("Device names cannot contain a slash.")
    if not name.isprintable():
        raise TwoFactorError("Device names cannot contain control characters.")
    return name


class TotpCredentialStore:
    """A private, lock-guarded ``{device name: credential}`` file.

    Unconfirmed entries past ``PENDING_TTL`` are dropped on every read, and every gunicorn
    worker shares one view through an exclusive lock.
    """

    FILENAME = ".totp-credentials.json"

    def __init__(self, bench: Bench) -> None:
        self._path = bench.path / self.FILENAME

    def all(self) -> dict[str, dict]:
        """Live credentials. Abandoned enrollments are filtered out, not rewritten.

        Verification reads this on every sign-in, so it must never write: the writers
        below already drop expired entries when they rewrite the file anyway.
        """
        return self._prune(self._load_raw())

    def confirmed(self) -> dict[str, dict]:
        """Credentials someone has proved they hold. Only these can authenticate."""
        return {key: entry for key, entry in self.all().items() if entry.get("confirmed_at")}

    def add(self, name: str, secret: str) -> str:
        """Register a pending credential under its name, which must be free."""
        with exclusive_file_lock(self._path):
            entries = self._prune(self._load_raw())

            if len(entries) >= MAX_ENROLLED_DEVICES:
                raise TwoFactorError(
                    f"This bench allows {MAX_ENROLLED_DEVICES} devices. "
                    "Remove one before adding another, or share an existing "
                    "device's setup key with another authenticator app."
                )

            if any(existing.casefold() == name.casefold() for existing in entries):
                raise TwoFactorError(f"A device named {name!r} is already enrolled.")

            entries[name] = {
                "secret": secret,
                "created_at": int(time.time()),
                "confirmed_at": None,
                "last_timestep": 0,
                "last_used_at": None,
            }
            self._write(entries)
        return name

    def confirm(self, name: str, timestep: int) -> bool:
        """Mark a credential usable, burning the time step that proved it."""
        with exclusive_file_lock(self._path):
            entries = self._prune(self._load_raw())
            entry = entries.get(name)
            if entry is None or entry.get("confirmed_at"):
                return False
            now = int(time.time())
            entry.update(confirmed_at=now, last_timestep=timestep, last_used_at=now)
            self._write(entries)
        return True

    def burn_timestep(self, name: str, timestep: int) -> bool:
        """Spend a time step for one credential, rejecting any step already used.

        Checked and written under a single lock: two workers reading the same stale value
        would otherwise both accept the same code.
        """
        with exclusive_file_lock(self._path):
            entries = self._prune(self._load_raw())
            entry = entries.get(name)
            if entry is None or timestep <= int(entry.get("last_timestep", 0)):
                return False
            entry.update(last_timestep=timestep, last_used_at=int(time.time()))
            self._write(entries)
        return True

    def remove(self, name: str) -> bool:
        with exclusive_file_lock(self._path):
            entries = self._prune(self._load_raw())
            if entries.pop(name, None) is None:
                return False
            self._write(entries)
        return True

    def _write(self, entries: dict[str, dict]) -> None:
        replace_private_text_locked(self._path, json.dumps(entries))

    def _load_raw(self) -> dict[str, dict]:
        """Raw ``{id: credential}`` from disk; empty if missing or unreadable."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _prune(entries: dict[str, dict]) -> dict[str, dict]:
        cutoff = int(time.time()) - PENDING_TTL
        return {
            key: entry
            for key, entry in entries.items()
            if isinstance(entry, dict)
            and (entry.get("confirmed_at") or int(entry.get("created_at", 0)) > cutoff)
        }


class TwoFactorAuthentication:
    """Enrolls devices and verifies their codes for one bench.

    A secret is written once per device and never rotated implicitly: it lives on that
    phone, so regenerating it silently would lock the device out.
    """

    def __init__(self, bench: Bench) -> None:
        self.bench = bench
        self.store = TotpCredentialStore(bench)

    @property
    def is_enabled(self) -> bool:
        """Whether a second factor is required to sign in. Reads only - every sign-in
        asks this, so it must not write."""
        return bool(self.store.confirmed())

    def get_credentials(self) -> list[dict]:
        """Public rows for the settings UI. Never exposes a secret."""
        rows = [
            {
                "name": name,
                "confirmed": bool(entry.get("confirmed_at")),
                "confirmed_at": entry.get("confirmed_at"),
                "created_at": entry.get("created_at"),
                "last_used_at": entry.get("last_used_at"),
            }
            for name, entry in self.store.all().items()
        ]
        return sorted(rows, key=lambda row: row["created_at"] or 0)

    def start_enrollment(self, name: str) -> dict:
        """Create a pending credential and return what an authenticator app needs.

        The secret is returned exactly here. Once confirmed it is never handed back, so a
        stolen session cannot clone an existing device's factor.
        """
        import pyotp

        name = validate_device_name(name)
        secret = pyotp.random_base32()
        self.store.add(name, secret)
        return {
            "name": name,
            "secret": secret,
            "provisioning_url": self._provisioning_url(name, secret),
        }

    def confirm_enrollment(self, name: str, otp: str) -> bool:
        """Activate a pending credential, but only once a code proves it was set up."""
        entry = self.store.all().get(name)
        if entry is None or entry.get("confirmed_at"):
            return False
        timestep = self._matching_timestep(entry["secret"], otp.strip())
        if timestep is None:
            return False
        return self.store.confirm(name, timestep)

    def remove_credential(self, name: str) -> bool:
        """Forget a device, and the recovery codes too once the last one is gone.

        Removal is the only thing that can switch 2FA off, so the cleanup belongs here
        rather than in whatever happens to ask about it next.
        """
        removed = self.store.remove(name)
        if removed and not self.is_enabled:
            self._store_recovery_codes([])
        return removed

    @property
    def unused_recovery_code_count(self) -> int:
        return len(self.bench.config.admin.recovery_codes)

    def generate_recovery_codes(self) -> list[str]:
        """Issue a fresh set and return it, the only time these are handed out.

        Replaces any previous set, so codes someone wrote down stop working the moment a
        new set is issued.
        """
        codes = [secrets.token_urlsafe(10) for _ in range(RECOVERY_CODE_COUNT)]
        self._store_recovery_codes(codes)
        return codes

    def redeem_recovery_code(self, code: str) -> bool:
        """Spend one recovery code. Each works once."""
        code = code.strip()
        # compare_digest raises on non-ASCII, so a pasted smart quote would 500 the login.
        if not code or not code.isascii():
            return False
        with BenchConfig.open(self.bench.path, mode="rw") as config:
            remaining = [
                stored for stored in config.admin.recovery_codes if not hmac.compare_digest(stored, code)
            ]
            # Guard against invalid recovery code as well.
            if len(remaining) == len(config.admin.recovery_codes):
                return False
            config.admin.recovery_codes = remaining
        self.bench.config.admin.recovery_codes = remaining
        return True

    def _store_recovery_codes(self, codes: list[str]) -> None:
        with BenchConfig.open(self.bench.path, mode="rw") as config:
            config.admin.recovery_codes = list(codes)
        self.bench.config.admin.recovery_codes = list(codes)

    def verify_second_factor(self, code: str) -> bool:
        """Accept either an authenticator code or a recovery code from the sign-in form.

        One entry point so the login screen has a single field: a six-digit code never
        collides with a recovery code, so trying both is unambiguous.
        """
        return self.verify_otp(code) or self.redeem_recovery_code(code)

    def verify_otp(self, otp: str) -> bool:
        """Accept a code from any enrolled device, burning that device's time step."""
        otp = otp.strip()
        # compare_digest raises on non-ASCII, so a pasted smart quote would 500 the login.
        if not otp or not otp.isascii():
            return False
        for name, entry in self.store.confirmed().items():
            timestep = self._matching_timestep(entry["secret"], otp)
            if timestep is not None:
                return self.store.burn_timestep(name, timestep)
        return False

    def _provisioning_url(self, name: str, secret: str) -> str:
        import pyotp

        return pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name=f"Pilot - {self.bench.config.name}")

    @staticmethod
    def _matching_timestep(secret: str, otp: str) -> int | None:
        """The time step whose code equals ``otp``, or None. Compared in constant time."""
        import pyotp

        totp = pyotp.TOTP(secret)
        now = int(time.time())
        for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
            moment = now + offset * totp.interval
            if hmac.compare_digest(totp.at(moment), otp):
                return moment // totp.interval
        return None
