from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request


def get_client_address(request: Request) -> str:
    """The caller's address. Every request has one over TCP."""
    if not request.client:
        raise HTTPException(403, "The Pilot agent accepts only the app VM.")
    return request.client.host


ClientAddress = Annotated[str, Depends(get_client_address)]


def authorize(request: Request, client_address: ClientAddress) -> None:
    """Refuse a caller without the token, or one other than the app VM."""
    agent = request.app.state.agent_config
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not agent.token_hash or not hmac.compare_digest(digest, agent.token_hash):
        raise HTTPException(401, "The Pilot agent token is wrong.")
    if not _is_same_address(client_address, agent.client_address):
        raise HTTPException(403, "The Pilot agent accepts only the app VM.")


def _is_same_address(left: str, right: str) -> bool:
    """Whether two addresses are one host, however each is written."""
    try:
        return ipaddress.ip_address(left) == ipaddress.ip_address(right)
    except ValueError:
        return False
