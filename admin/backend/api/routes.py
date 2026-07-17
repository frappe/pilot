from __future__ import annotations

API_ROOT_PREFIX = "/api"
API_V1_PREFIX = f"{API_ROOT_PREFIX}/v1"


def is_api_path(path: str) -> bool:
    return path == API_ROOT_PREFIX or path.startswith(f"{API_ROOT_PREFIX}/")
