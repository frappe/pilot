from dataclasses import dataclass


@dataclass
class AgentConfig:
    """The Pilot agent on a VM that another VM's Pilot manages, such as a dedicated database VM."""

    address: str = "::"
    port: int = 7781
    # SHA-256 of the bearer token, so the token itself never rests on this VM.
    token_hash: str = ""
    # The app VM. The agent refuses every other caller, whatever token it holds.
    client_address: str = ""
