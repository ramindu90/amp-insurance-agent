"""Agent Manager "Chat Agent" contract: POST /chat {message, session_id,
context} -> {response}, on port 8000."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agent import build_agent, load_mcp_tools
from config import Config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("insurance-agent")

CONFIG = Config.from_env()

# Tools are loaded lazily (and retried on the next request if the MCP proxy
# was unreachable), so a cold Devant endpoint (scale-to-zero) or a not-yet-
# attached Tool Configuration never crash-loops the pod.
_agent: Any = None
_tool_names: list[str] = []
_lock = asyncio.Lock()


async def get_agent() -> Any:
    global _agent, _tool_names
    async with _lock:
        if _agent is not None and (_tool_names or not CONFIG.mcp_url):
            return _agent
        try:
            tools = await load_mcp_tools(CONFIG)
        except Exception:  # noqa: BLE001
            log.exception("MCP tool discovery failed; continuing without tools")
            tools = []
        _tool_names = [t.name for t in tools]
        _agent = build_agent(CONFIG, tools)
        log.info(
            "agent ready (llm=%s, model=%s, mcp_tools=%s)",
            "agent-manager-proxy" if CONFIG.use_llm_provider else "openai-direct",
            CONFIG.model,
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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "agent_version": CONFIG.agent_version,
        "llm": (
            "agent-manager-proxy" if CONFIG.use_llm_provider
            else "openai-direct" if CONFIG.openai_api_key else "not-configured"
        ),
        "mcp_configured": bool(CONFIG.mcp_url),
        "mcp_tools": _tool_names,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    if not CONFIG.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="No LLM configured: attach an LLM Configuration in Agent Manager "
            "(env LLM_PROVIDER_URL/LLM_PROVIDER_KEY) or set OPENAI_API_KEY",
        )
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
