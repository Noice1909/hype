# DIVA — Data Intelligence Virtual Assistant

Enterprise multi-agent chat system that routes questions to specialized data agents via MCP.

## Architecture

```
User ──▶ FastAPI
              │
         LangGraph Orchestrator
         ├── Intake ── Context Manager (drift detection, entity tracking, token budgets)
         ├── Router ── LLM intent classifier → selects agent(s)
         ├── Dispatcher ── parallel or sequential fan-out
         ├── Agent Executor ── MCP tool-calling loop per agent
         ├── Synthesizer ── merge results + follow-up suggestions
         └── Evaluator ── fire-and-forget DeepEval (non-blocking)
```

## Agents

| Agent | Data Source | Transport | Scope |
|-------|-----------|-----------|-------|
| **diva** | — | — | Greetings, general chat, memory, session summaries |
| **neo4j** | Neo4j Knowledge Graph | HTTP | Domains, applications, teams, platforms, migrations, data lineage |
| **mongodb** | MongoDB | HTTP | Operational data, collections, documents, aggregations |
| **oracle** | Oracle DB | stdio | Relational data, reports, analytics |
| **dataplex** | Google Dataplex | HTTP | Data catalog, quality scores, governance |
| **github** | GitHub Enterprise | stdio | Repos, PRs, commits, issues |
| **confluence** | Confluence | SSE | Documentation, runbooks, wikis |
| **jira** | Jira | SSE | Tickets, sprints, epics |
| **autosys** | Autosys | stdio | Batch jobs, schedules, history |

## API

### `POST /query`

Non-streaming query.

**Request:**
```json
{
  "query": "What domains exist in the organization?",
  "conversation_id": "optional-uuid",
  "stream": false,
  "cypher": null
}
```

**Response:**
```json
{
  "request_id": "uuid",
  "conversation_id": "uuid",
  "response": "The organization has 9 domains: ...",
  "agent": "neo4j",
  "loop_used": "langgraph",
  "turns_used": 3,
  "duration_ms": 4500.0,
  "tools_called": ["get_schema", "run_cypher"],
  "cypher_queries": ["MATCH (d:Domain) RETURN d.name"],
  "events": [...],
  "suggestions": [{"text": "What applications are in Cloud Security?"}]
}
```

### `POST /query/stream`

SSE streaming — same request body.

**SSE Events:**
```
event: start           data: {"conversation_id": "uuid"}
event: routing         data: {"agents": ["neo4j"], "mode": "parallel"}
event: agent_result    data: {"agent_id": "neo4j", "status": "success", "latency_ms": 3200}
event: chunk           data: {"text": "The organization has 9 domains..."}
event: result          data: {"request_id": "...", "response": "...", "duration_ms": 4500}
event: done            data: {}
event: suggestions     data: {"suggestions": [{"text": "..."}]}
```

### Other Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/conversations/{id}` | Get conversation history |
| `DELETE` | `/conversations/{id}` | Delete conversation |
| `POST` | `/feedback` | Submit thumbs up/down |
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe (checks DB + MCP) |

## Quick Start

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/Scripts/activate  # Windows
# source .venv/bin/activate    # Linux

# 2. Install dependencies
pip install -r requirements.txt
pip install -e .

# 3. Configure
cp .env.example .env
# Edit .env with your credentials

# 4. Start MCP servers (separate terminals)
python neo4j/server.py --transport http --port 3006
# MongoDB MCP on port 8080

# 5. Start DIVA
uvicorn src.diva.main:app --reload
```

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Environment variables (LLM provider, DB credentials, MCP URLs) |
| `configs/agents.yaml` | Agent registry — names, descriptions, scopes, MCP mappings |
| `configs/mcp_servers.yaml` | MCP server connections (URL, transport, headers) |
| `configs/context.yaml` | Token budgets, sliding window size, drift thresholds |
| `configs/evaluation.yaml` | DeepEval metric thresholds |

## LLM Provider

Controlled by `LLM_PROVIDER` env var:

- `ollama` — Local development (default). Set `OLLAMA_MODEL`.
- `tachyon` — Production. Uses `tachyon_langchain_client/` with APIGEE OAuth2.

## Project Structure

```
src/
├── tachyon_langchain_client/   # Production LLM client (do not modify)
└── diva/
    ├── main.py                 # FastAPI app + lifespan
    ├── api/                    # Endpoints + middleware
    ├── graph/                  # LangGraph nodes + state
    ├── agents/                 # Registry + prompt templates
    ├── context/                # Drift detection, entity tracking, summarizer
    ├── mcp/                    # MCP client (HTTP/SSE/stdio) + tool adapter
    ├── evaluation/             # DeepEval + Ragas
    ├── llm/                    # Provider factory
    └── storage/                # MongoDB (Motor async)
```

## Context Management

Three-layer drift detection minimizes false positives:

1. **Fast heuristic** — keyword/entity overlap (no LLM call)
2. **LLM classification** — only for ambiguous cases
3. **Severity scoring** — `none` / `soft` (keep context) / `hard` (full reset)

## Adding New Data Sources

Config-only — no code changes:

1. Add agent entry in `configs/agents.yaml`
2. Add MCP server entry in `configs/mcp_servers.yaml`
3. Optionally add a prompt template in `src/diva/agents/prompt_templates/`
