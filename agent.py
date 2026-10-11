"""LangGraph ReAct agent: LLM via the Agent Manager LLM proxy, tools via the
Agent Manager MCP proxy (upstream: insurance MCP server on WSO2 Devant)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from config import Config

log = logging.getLogger("insurance-agent")

SYSTEM_PROMPT = (
    "You are the HealthGuard insurance assistant. You help policyholders and "
    "insurer staff with health insurance policies and claims.\n\n"
    "Your tools come from the HealthGuard insurance MCP server (Model Context "
    "Protocol), reached through WSO2 Agent Manager. If asked whether you have "
    "MCP access, answer from the tools you actually have: list them if any are "
    "available, otherwise say the MCP server is currently unreachable.\n"
    "Use the available tools for every factual answer — never invent policy "
    "numbers, amounts, dates or claim statuses. Policyholders are identified by "
    "their email address (the tools call it 'username'); ask for it if you do "
    "not have it.\n"
    "Before submitting a claim, confirm claim type, amount and description with "
    "the user. Valid claim types: Outpatient, Hospitalization, Emergency, "
    "Pharmacy, DME, Medical.\n"
    "Only change a policy or claim status when the user explicitly asks for it, "
    "and state the exact change you made.\n"
    "If no tools are available, say the insurance system is unreachable rather "
    "than guessing."
)


async def load_mcp_tools(cfg: Config) -> list[Any]:
    """Discover tools from the MCP proxy. The agent only holds proxy-level
    credentials (an API key, or an AgentID token); any upstream credential is
    added by the gateway."""
    if not cfg.mcp_url:
        log.warning("No MCP URL injected; starting without MCP tools")
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    if cfg.mcp_auth == "oauth" and not cfg.agentid_ready:
        # AgentID provisioning can land after the first deploy; tool
        # discovery is retried on the next /chat.
        log.warning("MCP_AUTH=oauth but AMP_AGENTID_* not injected yet; no MCP tools")
        return []

    servers: dict[str, dict[str, Any]] = {}
    for i, url in enumerate(cfg.mcp_urls):
        connection: dict[str, Any] = {"url": url, "transport": "streamable_http"}
        if cfg.mcp_auth == "apikey":
            # Header = the MCP server's Security > API Key header (default X-API-Key).
            connection["headers"] = {cfg.mcp_api_key_header: cfg.mcp_api_key}
        elif cfg.mcp_auth == "oauth":
            from agent_identity import AgentIdentityAuth

            # One auth per URL: each token is minted with that URL as resource.
            connection["auth"] = AgentIdentityAuth(cfg, resource=url)
        servers["insurance" if i == 0 else f"insurance_{i}"] = connection
    client = MultiServerMCPClient(servers)
    return list(await client.get_tools())


def build_llm(cfg: Config) -> ChatOpenAI:
    if cfg.use_llm_provider:
        # The AMP LLM proxy authenticates with the "API-Key" header; blank out
        # Authorization so the SDK's dummy bearer token is never forwarded.
        return ChatOpenAI(
            model=cfg.model,
            temperature=0,
            base_url=cfg.llm_provider_url,
            api_key="not-used",
            default_headers={"API-Key": cfg.llm_provider_key, "Authorization": ""},
        )
    return ChatOpenAI(model=cfg.model, temperature=0, api_key=cfg.openai_api_key)


def build_agent(cfg: Config, tools: list[Any]) -> Any:
    # Conversation memory keyed on the chat session_id (thread_id).
    return create_react_agent(
        model=build_llm(cfg),
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )
