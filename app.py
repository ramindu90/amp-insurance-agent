"""Agent Manager "Chat Agent" contract: POST /chat {message, session_id,
context} -> {response}, on port 8000."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agent import build_agent, load_mcp_tools

# Present when Agent Manager runs the agent with auto-instrumentation; absent
# in a plain local run (same pattern as AMP's samples/crewai-agent).
try:
    from traceloop.sdk import Traceloop
except ImportError:
    Traceloop = None
from config import Config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("insurance-agent")

CONFIG = Config.from_env()

# Tools are loaded lazily (and retried on the next request if the MCP proxy
# was unreachable), so a cold Devant endpoint (scale-to-zero) or a not-yet-
# attached Tool Configuration never crash-loops the pod.
_agent: Any = None
_tool_names: list[str] = []
_mcp_error: str = ""
_lock = asyncio.Lock()


def _root_cause(exc: BaseException) -> str:
    """MCP client errors arrive wrapped in anyio ExceptionGroups; surface the
    innermost one (e.g. the HTTP 401/404 from the gateway)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


async def get_agent() -> Any:
    global _agent, _tool_names, _mcp_error
    async with _lock:
        if _agent is not None and (_tool_names or not CONFIG.mcp_url):
            return _agent
        _mcp_error = ""
        try:
            tools = await load_mcp_tools(CONFIG)
        except Exception as exc:  # noqa: BLE001
            _mcp_error = _root_cause(exc)
            log.exception("MCP tool discovery failed (%s): %s", CONFIG.mcp_url, _mcp_error)
            tools = []
        _tool_names = [t.name for t in tools]
        _agent = build_agent(CONFIG, tools)
        log.info(
            "agent ready (llm=%s, model=%s, mcp_auth=%s, mcp_tools=%s)",
            "agent-manager-proxy" if CONFIG.use_llm_provider else "openai-direct",
            CONFIG.model,
            CONFIG.mcp_auth,
            _tool_names,
        )
        return _agent


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str | None = None


app = FastAPI(title="Insurance Agent (Agent Manager)", version="0.1.0")


def _injected_env_names() -> list[str]:
    """Names (never values) of URL/key env vars present in the pod, to show
    what Agent Manager actually injected when the expected names are missing."""
    return sorted(
        n for n in os.environ
        if n.endswith(("_URL", "_KEY", "_API_KEY")) and not n.startswith("AMP_")
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "agent_version": CONFIG.agent_version,
        "llm": (
            "agent-manager-proxy" if CONFIG.use_llm_provider
            else "openai-direct" if CONFIG.openai_api_key else "not-configured"
        ),
        "mcp_url": CONFIG.mcp_url,
        "mcp_auth": CONFIG.mcp_auth,
        "mcp_key_header": CONFIG.mcp_api_key_header if CONFIG.mcp_auth == "apikey" else None,
        "mcp_tools": _tool_names,
        "mcp_error": _mcp_error,
        "url_key_env_vars": _injected_env_names(),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    if not CONFIG.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="No LLM configured: attach an LLM Configuration in Agent Manager "
            "and name its env vars LLM_PROVIDER_URL/LLM_PROVIDER_KEY, then redeploy. "
            f"URL/key env vars present in this pod: {_injected_env_names() or 'none'}",
        )
    if Traceloop is not None:
        # Tags this request's spans with the chat session so the Traces page's
        # Conversation column can group and filter a conversation's turns.
        Traceloop.set_association_properties({"session_id": session_id})
    agent = await get_agent()
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=req.message)]},
            config={"configurable": {"thread_id": session_id}},
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("agent invocation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    final: Any = next(
        (m.content for m in reversed(result.get("messages", [])) if isinstance(m, AIMessage)),
        "(no response)",
    )
    if isinstance(final, list):
        final = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in final)
    return ChatResponse(response=str(final), session_id=session_id)
