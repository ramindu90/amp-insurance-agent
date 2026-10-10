"""Environment-driven configuration.

Agent Manager injects each attached LLM provider / MCP server as a pair of
env vars, <PREFIX>_URL + <PREFIX>_API_KEY, where the prefix depends on where
it was attached (agent-services/agent_configuration_service.go and the
console's create form):
  create form, LLM : <AGENT_DISPLAY_NAME>_<n>_URL / _API_KEY
  create form, MCP : <AGENT_DISPLAY_NAME>_MCP_<n>_URL / _API_KEY
  Configure page   : <CONFIG_OR_MAPPING_NAME>_URL / _API_KEY
  or any name typed into the console's "variable name" fields.
So no names are fixed. Explicit LLM_PROVIDER_* / INSURANCE_MCP_* win if set;
otherwise the pairs are discovered: a URL ending in /mcp is the MCP proxy
(its API key is optional — absent when the proxy has no API-key security),
any other URL with a sibling _API_KEY is the LLM proxy. Locally, the same
variables can point straight at OpenAI and at Devant (see .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _injected_pairs() -> list[tuple[str, str, str]]:
    """(prefix, url, api_key) for every <PREFIX>_URL in the environment."""
    pairs = []
    for name, value in sorted(os.environ.items()):
        if name.endswith("_URL") and not name.startswith("AMP_") and value.strip():
            prefix = name[: -len("_URL")]
            pairs.append((prefix, value.strip(), _env(f"{prefix}_API_KEY")))
    return pairs


def _is_mcp_url(url: str) -> bool:
    return url.rstrip("/").endswith("/mcp")


def _detect_mcp_env() -> tuple[str, str]:
    for _, url, key in _injected_pairs():
        if _is_mcp_url(url):
            return url, key
    return "", ""


def _detect_llm_env() -> tuple[str, str]:
    for _, url, key in _injected_pairs():
        if key and not _is_mcp_url(url):
            return url, key
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
        llm_url, llm_key = _env("LLM_PROVIDER_URL"), _env("LLM_PROVIDER_KEY")
        if not (llm_url and llm_key):
            llm_url, llm_key = _detect_llm_env()
        return cls(
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            llm_provider_url=llm_url,
            llm_provider_key=llm_key,
            openai_api_key=_env("OPENAI_API_KEY"),
            mcp_url=mcp_url,
            mcp_api_key=mcp_api_key,
            # AMP MCP proxies default to X-API-Key (the LLM proxy uses API-Key).
            # When pointing straight at Devant locally, set this to "api-key".
            mcp_api_key_header=_env("INSURANCE_MCP_API_KEY_HEADER", "X-API-Key"),
            agent_version=_env("AGENT_VERSION", "dev"),
        )
