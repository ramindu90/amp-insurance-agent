"""AgentID bearer tokens for OAuth-secured MCP proxies.

When the MCP server's Security in Agent Manager is OAuth (AgentID), no MCP API
key is injected. Instead every platform-hosted agent gets the fixed
AMP_AGENTID_CLIENT_ID / _CLIENT_SECRET / _TOKEN_ENDPOINT / _SCOPES env vars;
this module turns them into a client_credentials access token scoped (RFC 8707
``resource``) to the MCP URL and attaches it as ``Authorization: Bearer`` to
every MCP request, re-minting it before it expires. Same pattern as Agent
Manager's samples/it-helpdesk-agent/agent_identity.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

import httpx

from config import Config

log = logging.getLogger("insurance-agent")


class AgentIdentityAuth(httpx.Auth):
    """One instance per MCP URL — the token is only valid for that resource."""

    def __init__(self, cfg: Config, resource: str) -> None:
        self._cfg = cfg
        self._resource = resource
        self._token: str | None = None
        self._expiry = 0.0
        # Coalesces concurrent refreshes onto a single token-endpoint call.
        self._lock = asyncio.Lock()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._get_token()}"
        yield request

    async def _get_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expiry:
                return self._token
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self._cfg.agentid_token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "scope": self._cfg.agentid_scopes,
                        "resource": self._resource,
                    },
                    auth=(self._cfg.agentid_client_id, self._cfg.agentid_client_secret),
                )
                response.raise_for_status()
                token_data = response.json()
            self._token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 300))
            # Refresh at 75% of the lifetime so no request goes out on a token
            # that expires mid-flight.
            self._expiry = time.monotonic() + expires_in * 0.75
            log.info("Minted AgentID access token for %s (expires_in=%s)", self._resource, expires_in)
            return self._token
