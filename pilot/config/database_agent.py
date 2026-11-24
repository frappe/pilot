from dataclasses import dataclass


@dataclass
class DatabaseAgentConfig:
    """The database agent on a dedicated database VM, which the app VM's Pilot manages MariaDB through."""

    address: str = "::"
    port: int = 7781
    # SHA-256 of the bearer token, so the token itself never rests on this VM.
    token_hash: str = ""
    # The app VM. The agent refuses every other caller, whatever token it holds.
    client_address: str = ""
