# Insurance agent on WSO2 Agent Manager (Python)

A LangGraph ReAct agent, deployed as a **platform-hosted Chat Agent** in WSO2
Agent Manager (AMP), that:

- calls **OpenAI through an AMP LLM Service Provider** (AMP's AI gateway
  issues a per-agent LLM proxy; the agent never sees the OpenAI key), and
- uses tools from the **insurance MCP server hosted on WSO2 Cloud – Integration
  Platform (Devant)**, reached through an **AMP MCP proxy** (the agent never
  sees the Devant API key either).

```
 Try It / client ──► AMP agent (this repo, POST /chat :8000)
                        │  API-Key: <llm proxy key>          │  X-API-Key: <mcp proxy key>
                        ▼                                    ▼
              AMP AI gateway: LLM proxy          AMP AI gateway: MCP proxy
                        │  Authorization: Bearer sk-…        │  api-key: <Devant key>
                        ▼                                    ▼
                 api.openai.com/v1           Devant: insurancemcp (MCP Service)
                                             (/mcp on :8084, 7 tools)
```

The MCP server is the same code as the k8s demo's
`k8-artefacts-apim-bi-elk/use_cases/insurance/mock_insurance_backend/mcp_server.bal`,
copied into its own Devant integration
`/Users/ramindu/WSO2Integrator/ipaas/insurancemcp` (REST APIs: `../insuranceservice`).

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI, AMP Chat Agent contract: `POST /chat {message, session_id, context}` → `{response, session_id}`; `GET /health` |
| `agent.py` | LLM client (AMP proxy via `API-Key` header, or direct OpenAI) + MCP tool discovery (`langchain-mcp-adapters`) |
| `config.py` | Env vars (below) |
| `main.py` | `uvicorn` on `0.0.0.0:8000` — AMP start command is `python main.py` |

| Env var | Set by | Meaning |
|---|---|---|
| `LLM_PROVIDER_URL` / `LLM_PROVIDER_KEY` | AMP (LLM Configuration — rename to these names) | LLM proxy base URL / key |
| `INSURANCE_MCP_URL` / `INSURANCE_MCP_API_KEY` | AMP (Tool Configuration — rename to these names) | MCP proxy URL / key |
| `INSURANCE_MCP_API_KEY_HEADER` | you (optional) | default `X-API-Key` (AMP MCP proxy default). Change only if you changed the proxy's Security header |
| `OPENAI_MODEL` | you (optional) | default `gpt-4o-mini` |
| `OPENAI_API_KEY` | local runs only | direct OpenAI fallback when `LLM_PROVIDER_*` are unset |

The pod starts even with nothing configured (`/health` shows `"llm":"not-configured"`,
`/chat` returns 503) so the first deploy doesn't crash-loop before you attach
the LLM. MCP tools are discovered on the first `/chat` and re-tried on later
requests if discovery failed (e.g. Devant cold start).

---

## Step-by-step

### Part A — Deploy the insurance MCP server (and REST APIs) to Devant

Workspace: `/Users/ramindu/WSO2Integrator/ipaas` (GitHub `ramindu90/iPaaSMocks`),
two integrations:

| Integration (project path) | Devant type | What |
|---|---|---|
| `insurancemcp` | **MCP Service** | MCP server, `/mcp` on :8084, 7 tools — **what AMP proxies** |
| `insuranceservice` | Integration as API | REST: `insurance-customer-api` (:8082 `/insurance/customer`), `insurance-agent-api` (:8083 `/insurance/agent`) — optional, not used by the agent |

Each has its own in-memory data. `Ballerina.toml`'s `org = "ramindu"` must
match your Devant org handle.

1. Push the workspace to GitHub.
2. Devant console → project → import/create integration from the repo with
   **project path `insurancemcp`** and type **MCP Service** (do the same with
   `insuranceservice` / Integration as API if you want the REST APIs).
3. Wait for build + deploy to Development; test it in the console
   (Get Test Key → header `Test-Key` or `api-key` against the Development URL).
4. Security: keep/enable **API Key** (header `api-key`) if the type offers it.
5. **Promote to Production**, then **Lifecycle → Publish** if it isn't already
   Published. It should now appear under Developer Portal → **MCP Servers**.
6. Developer Portal → MCP Servers → *Insurance MCP Server* → **Subscribe**
   (application e.g. `agent-manager`) → Applications → `agent-manager` →
   **Manage Keys** → **Generate Key** in the row for *this* MCP server (keys are
   per API — a key generated for another API gets 403 `900908 API Subscription
   validation failed`).
7. Copy the **Production** URL of the MCP endpoint from the Production card.
8. Verify (cold start after scale-to-zero can take ~30s):

   ```bash
   MCP=<production URL>
   curl -s -X POST "$MCP" -H "api-key: $DEVANT_KEY" \
     -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   Expect 7 tools. 401 = no/invalid key, 403 900908 = key not subscribed to
   this API. If 404, try with/without a trailing `/mcp` or `/` (depends on the
   base path Devant derives for the MCP Service type).

### Part B — Agent Manager: LLM Service Provider (OpenAI)

1. AMP console → Organization level → **RESOURCES › LLM Service Providers** →
   **+ Add Service Provider**.
2. Name `insurance-openai`, Version `v1`, Context `/insurance-openai`,
   template **OpenAI**, API key = your OpenAI key → **Add provider**.
3. Provider **Access Control** tab: make sure `gpt-4o-mini` (or whatever you set
   in `OPENAI_MODEL`) is allowed.

### Part C — Agent Manager: register the Devant MCP server (MCP proxy)

1. Organization → **Resources › MCP Servers** → **Register MCP Server**
   (older docs call it *MCP Proxies › Add MCP Proxy*).
2. Name `insurance-mcp`, Version `v1`, Context `/insurance`.
3. **Add Endpoint** → MCP endpoint URL = the Devant Production URL from A.8.
4. **Advanced Configurations** → Header `api-key`, Value `<Devant API key from A.7>`
   (the gateway adds this upstream; agents never see it).
5. Select the environment(s) → **Fetch Server Info** (should list the 7 tools;
   if it times out, re-run the curl in A.9 to warm Devant and retry) → **Add**
   → **Create**.
6. Proxy **Security** tab: leave **API Key** (default header `X-API-Key`).
   Optional: **Manage Tools** to deny the admin tools (`updatePolicyStatus`,
   `updateClaim`, `listAllPolicies`, `getAllClaims`) for a customer-only agent.

### Part D — Agent Manager: deploy this Python agent

Prerequisite: this repo is pushed to `https://github.com/ramindu90/amp-insurance-agent`
(if the repo is private, add a **Git Secret** with a GitHub PAT in step 3).

1. **Projects** → open/create a project (e.g. `insurance`).
2. **Add Agent** → **Platform-Hosted Agent** → **Source Code**.
3. Fill in:
   - Name `insurance-python-agent`
   - GitHub Repository `https://github.com/ramindu90/amp-insurance-agent`, Branch `main`
   - Project Path `/`
   - Build: **Python**, Language Version `3.12`, Start Command `python main.py`
   - **Enable auto instrumentation**: on (traces in the Traces tab)
   - Agent type **Chat Agent** (`/chat` on port 8000)
   - Env vars (optional): `OPENAI_MODEL=gpt-4o-mini`
   - MCP Proxies (optional section): you can pick `insurance-mcp` here already
4. **Deploy**. Wait for Build → Deploy to finish. `GET /health` →
   `"llm":"not-configured"` is expected at this point.

### Part E — Attach the LLM and the MCP proxy to the agent

1. Agent → **Configure** → **LLM Configurations** → **+ Add LLM Configuration** →
   **Select a Service Provider** → `insurance-openai` → **Save**.
   In the *Environment Variables & Integration Guide* panel rename the
   variables to **`LLM_PROVIDER_URL`** and **`LLM_PROVIDER_KEY`** (defaults are
   `<CONFIG>_URL` / `<CONFIG>_API_KEY`).
2. **Configure** → **Tool Configurations** → **Add Tool Configuration** →
   `insurance-mcp` → set Environment Variable Names to
   **`INSURANCE_MCP_URL`** and **`INSURANCE_MCP_API_KEY`** → **Save**.
3. **Redeploy** the agent (Deploy page) — MCP values are injected at deploy time.

### Part F — Test

1. Agent → **Try It**:
   - "What is my policy and do I have any claims? My email is sam@gmail.com"
   - "Submit an Outpatient claim of 120 USD for a GP visit." → confirm
   - "Show me all pending claims" (admin tool, if not denied in C.6)
2. **Traces** tab: you should see the OpenAI chat-completion spans and the MCP
   tool calls.
3. **Runtime Logs**: look for `agent ready (llm=agent-manager-proxy, … mcp_tools=[…7 names…])`.

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| `/chat` 503 "No LLM configured" | LLM Configuration not attached, or env var names not renamed to `LLM_PROVIDER_URL`/`LLM_PROVIDER_KEY` |
| Answers "insurance system is unreachable", `mcp_tools: []` in `/health` | Tool Configuration missing/renamed wrong, or Devant cold/401 — check Runtime Logs for the discovery error |
| MCP 401 from AMP gateway | header mismatch: agent sends `X-API-Key`; if you changed the proxy Security header, set `INSURANCE_MCP_API_KEY_HEADER` |
| MCP 401 from Devant (via proxy) | the `api-key` header/value in C.4 is wrong or the key was generated for another API |
| LLM 401/403 | model not allowed in provider Access Control, or wrong header (must be `API-Key`) |

## Run locally

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in; then:
set -a; . ./.env; set +a
python main.py
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id":"t1","message":"What is my policy? My email is sam@gmail.com"}'
```

To test without Devant, run the iPaaS package locally
(`cd /Users/ramindu/WSO2Integrator/ipaas/insurancemcp && bal run`) and set
`INSURANCE_MCP_URL=http://localhost:8084/mcp` with no key. Verified this way on
2026-10-09: 7 tools discovered; policy lookup, claims and multi-turn claim
submission all work.
