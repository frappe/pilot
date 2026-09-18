"""Central HTTP client and cloud-metadata credential pickup."""

from __future__ import annotations

from pilot.integrations.central.client import CentralClient, CentralClientError
from pilot.integrations.central.metadata import InstanceMetadata, apply_central_config

__all__ = [
    "CentralClient",
    "CentralClientError",
    "InstanceMetadata",
    "apply_central_config",
]
