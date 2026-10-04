from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class BenchResource(TypedDict):
    name: str
    port: int
    domain: str
    production: bool
    process_manager: str | None
    reachable: bool
    admin_url: str
    workload_running: bool | None
    admin_running: bool | None
    site_count: int


class CreatedBench(BenchResource):
    wizard_at_domain: bool
    setup_link: str
    scheme: NotRequired[Literal["http", "https"]]
    server_ip: NotRequired[str]


class BenchReadiness(TypedDict):
    ready: bool
