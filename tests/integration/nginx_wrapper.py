from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.managers.nginx import NginxConfigRenderer

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def catch_all_http_directives(bench: Bench) -> str:
    """The catch-all's http-level directives, such as log_format and $pilot_client_addr, that bench vhosts use."""
    server_config = NginxConfigRenderer(bench).generate_server_config(bench.path / "error_pages")
    return server_config.split("server {", 1)[0]
