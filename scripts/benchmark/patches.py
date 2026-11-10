"""Neutralize host-touching side effects for in-process benchmarks.

The harness runs against a synthetic bench in a temp directory. These patches
keep cases from creating the real admin venv, reading root-only certificates,
taking the host-wide nginx lock, or refreshing the marketplace registry over
the network. Subprocess CLI cases run the unpatched CLI.
"""

from __future__ import annotations

from contextlib import nullcontext


def apply_patches() -> None:
    from pilot.core.bench import certificates
    from pilot.integrations.marketplace import Marketplace
    from pilot.managers.environment import AdminEnvManager
    from pilot.managers.nginx import NginxManager

    NginxManager.config_lock = lambda self: nullcontext()
    NginxManager.has_cert = lambda self, site: False
    AdminEnvManager.ensure = lambda self: None
    certificates.read_certificate = lambda cert_file: (None, [])
    # The registry refreshes itself over the network and writes a host-level
    # last-checked file. Benchmark the surrounding path with an empty index.
    Marketplace.registry_by_name = staticmethod(lambda: {})
