"""Environment-driven configuration.

On Agent Manager the LLM_* and INSURANCE_MCP_* values are injected by the
platform (LLM Configuration / Tool Configuration on the agent's Configure
page) — rename the console's default variable names to the ones below when
attaching them. Locally, the same variables can point straight at OpenAI and
at the Devant-hosted MCP endpoint (see .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _detect_mcp_env() -> tuple[str, str]:
    """Fallback when the Tool Configuration kept Agent Manager's default
    variable names (<NAME>_URL / <NAME>_API_KEY): pick the *_URL whose value
    is an MCP endpoint (ends with /mcp) and its sibling *_API_KEY."""
    for name, value in sorted(os.environ.items()):
        if name.endswith("_URL") and value.strip().rstrip("/").endswith("/mcp"):
            prefix = name[: -len("_URL")]
            return value.strip(), _env(f"{prefix}_API_KEY")
    return "", ""


@dataclass(frozen=True)
class Config:
    model: str
    # Agent Manager LLM proxy (OpenAI-compatible). Empty -> call OpenAI directly.
    llm_provider_url: str
    llm_provider_key: str
    openai_api_key: str
    # Agent Manager MCP proxy in front of the Devant-hosted insurance MCP server.
    mcp_url: str
    mcp_api_key: str
    mcp_api_key_header: str
    agent_version: str

    @property
    def use_llm_provider(self) -> bool:
        return bool(self.llm_provider_url and self.llm_provider_key)

    @property
    def llm_configured(self) -> bool:
        return self.use_llm_provider or bool(self.openai_api_key)

    @classmethod
    def from_env(cls) -> "Config":
        # Deliberately no hard failure here: the first Agent Manager deploy
        # usually happens before the LLM Configuration is attached, and a
        # crash-looping pod would block that. /chat reports it instead.
        mcp_url, mcp_api_key = _env("INSURANCE_MCP_URL"), _env("INSURANCE_MCP_API_KEY")
        if not mcp_url:
            mcp_url, mcp_api_key = _detect_mcp_env()
        return cls(
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            llm_provider_url=_env("LLM_PROVIDER_URL"),
            llm_provider_key=_env("LLM_PROVIDER_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
            mcp_url=mcp_url,
            mcp_api_key=mcp_api_key,
            # AMP MCP proxies default to X-API-Key (the LLM proxy uses API-Key).
            # When pointing straight at Devant locally, set this to "api-key".
            mcp_api_key_header=_env("INSURANCE_MCP_API_KEY_HEADER", "X-API-Key"),
            agent_version=_env("AGENT_VERSION", "dev"),
        )
