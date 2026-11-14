from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RevokeTOTP(Command):
    """Revoke a TOTP credential for any device as a rescue operation."""

    name: ClassVar[str] = "revoke-totp"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Revoke a TOTP credential for any device as a rescue operation."
    device_name: Annotated[str, Arg(help="Name of the device to revoke TOTP for.", required=True)]

    def run(self) -> None:
        """Remove the user's two-factor secret."""
        from admin.backend.internal.two_factor_authentication import TwoFactorAuthentication

        two_factor = TwoFactorAuthentication(self.bench)
        if not two_factor.remove_credential(self.device_name):
            self.report(f"No TOTP credential found for device '{self.device_name}'.")
            return

        self.report(f"TOTP credential for device '{self.device_name}' has been revoked.")
        if two_factor.is_enabled:
            self.report("Two-factor authentication is still required to sign in.")
            return

        self.report("That was the last device: two-factor authentication is now off,")
        self.report("and its recovery codes have been discarded.")
