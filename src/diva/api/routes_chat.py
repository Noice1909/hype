"""Query endpoints — aligned with agent_orchestrator API contract.

Endpoints:
  POST /query        — non-streaming
  POST /query/stream — SSE via EventSourceResponse
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from diva.schemas.chat import QueryRequest, QueryResponse
from diva.dependencies import get_graph
from diva.storage.mongo import save_message

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_initial_state(body: QueryRequest, conversation_id: str) -> dict:
    return {
        "session_id": conversation_id,
        "user_message": body.query,
        "turn_number": 0,
        "cypher_override": body.cypher,
        "running_summary": "",
        "entity_scratchpad": [],
        "sliding_window": [],
        "drift_detected": False,
        "previous_topic_summary": "",
        "routing_decision": {
            "agents": [], "execution_mode": "parallel",
            "reasoning": "", "sequential_plan": None,
        },
        "agent_results": [],
        "pending_agents": [],
        "final_response": "",
        "follow_up_suggestions": [],
        "sources": [],
        "eval_payload": {},
    }


def _extract_tools_called(result: dict) -> list[str]:
    tools = []
    for ar in result.get("agent_results", []):
        for tc in ar.get("tool_calls_made", []):
            tools.append(tc.get("tool", ""))
    return tools


def _extract_cypher_queries(result: dict) -> list[str]:
    queries = []
    for ar in result.get("agent_results", []):
        for tc in ar.get("tool_calls_made", []):
            if tc.get("tool") in ("run_cypher", "query_oracle"):
                args = tc.get("args", {})
                q = args.get("query", "")
                if q:
                    queries.append(q)
    return queries


def _normalize_suggestion_entry(fu) -> dict[str, str] | None:
    """Convert a single follow-up (str or dict) into the API shape."""
    if isinstance(fu, str):
        text = fu.strip()
        return {"text": text} if text else None
    if not isinstance(fu, dict):
        return None
    text = str(fu.get("text") or "").strip()
    if not text:
        return None
    entry: dict[str, str] = {"text": text}
    if fu.get("type"):
        entry["type"] = fu["type"]
    if fu.get("agent"):
        entry["agent"] = fu["agent"]
    return entry


def _build_suggestions(follow_ups: list) -> list[dict[str, str]]:
    """Normalize follow-ups to the API response shape.

    Accepts both legacy strings and structured dicts with {text, type, agent}.
    """
    out: list[dict[str, str]] = []
    for fu in follow_ups or []:
        entry = _normalize_suggestion_entry(fu)
        if entry is not None:
            out.append(entry)
    return out


def _extract_events(result: dict) -> list[dict]:
    events = []
    rd = result.get("routing_decision", {})
    if rd.get("agents"):
        events.append({
            "type": "routing",
            "data": {
                "agents": rd.get("agents", []),
                "mode": rd.get("execution_mode", ""),
                "reasoning": rd.get("reasoning", ""),
            },
            "timestamp": time.time(),
        })
    for ar in result.get("agent_results", []):
        events.append({
            "type": "agent_result",
            "data": {
                "agent_id": ar.get("agent_id", ""),
                "status": ar.get("status", ""),
                "latency_ms": ar.get("latency_ms", 0),
                "tools": [tc.get("tool", "") for tc in ar.get("tool_calls_made", [])],
            },
            "timestamp": time.time(),
        })
    return events


# ── POST /query ──────────────────────────────────────────────────────────────

@router.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request):
    """Process a query through the DIVA agent system."""
    graph = get_graph()
    if not graph:
        raise HTTPException(status_code=503, detail="System not initialized")

    conversation_id = body.conversation_id or str(uuid.uuid4())
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    start = time.perf_counter()

    result = await graph.ainvoke(_build_initial_state(body, conversation_id))

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

    # Persist messages
    turn = result.get("turn_number", 0)
    await save_message(
        session_id=conversation_id, turn_number=turn,
        role="user", content=body.query,
    )
    rd = result.get("routing_decision", {})
    agents_used = rd.get("agents", [])
    await save_message(
        session_id=conversation_id, turn_number=turn,
        role="assistant", content=result.get("final_response", ""),
        agents_used=agents_used, sources=result.get("sources", []),
        follow_ups=result.get("follow_up_suggestions", []),
        metadata={"total_ms": elapsed_ms},
    )

    return QueryResponse(
        request_id=request_id,
        conversation_id=conversation_id,
        response=result.get("final_response", ""),
        agent=",".join(agents_used),
        loop_used="langgraph",
        turns_used=result.get("turn_number", 0),
        duration_ms=elapsed_ms,
        tools_called=_extract_tools_called(result),
        cypher_queries=_extract_cypher_queries(result),
        events=_extract_events(result),
        suggestions=_build_suggestions(result.get("follow_up_suggestions", [])),
    )


# ── POST /query/stream ──────────────────────────────────────────────────────

@router.post("/query/stream")
async def query_stream(body: QueryRequest, request: Request):
    """Stream query processing events via SSE (EventSourceResponse)."""
    graph = get_graph()
    if not graph:
        raise HTTPException(status_code=503, detail="System not initialized")

    conversation_id = body.conversation_id or str(uuid.uuid4())
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    async def event_generator():
        start = time.perf_counter()

        yield {"event": "start", "data": json.dumps({
            "conversation_id": conversation_id,
            "request_id": request_id,
        })}

        initial_state = _build_initial_state(body, conversation_id)
        final_result = {}

        try:
            async for event in graph.astream_events(initial_state, version="v2"):
                event_name = event.get("event", "")
                node_name = event.get("name", "")

                if event_name == "on_chain_start" and node_name in _TRACKED_NODES:
                    yield {
                        "event": node_name,
                        "data": json.dumps({"status": "running"}),
                    }

                elif event_name == "on_chain_end":
                    output = event.get("data", {}).get("output", {})
                    if not isinstance(output, dict):
                        continue

                    if node_name == "router":
                        rd = output.get("routing_decision", {})
                        if rd:
                            yield {
                                "event": "routing",
                                "data": json.dumps({
                                    "agents": rd.get("agents", []),
                                    "mode": rd.get("execution_mode", "parallel"),
                                    "reasoning": rd.get("reasoning", ""),
                                }),
                            }

                    elif node_name == "agent_executor":
                        for ar in output.get("agent_results", []):
                            # Emit tool_call events for each tool used
                            for tc in ar.get("tool_calls_made", []):
                                yield {
                                    "event": "tool_call",
                                    "data": json.dumps({
                                        "tool": tc.get("tool", ""),
                                        "agent": ar.get("agent_id", ""),
                                    }),
                                }
                                yield {
                                    "event": "tool_result",
                                    "data": json.dumps({
                                        "tool": tc.get("tool", ""),
                                        "result": tc.get("result_preview", ""),
                                    }),
                                }
                            yield {
                                "event": "agent_done",
                                "data": json.dumps({
                                    "agent": ar.get("agent_id", ""),
                                    "status": ar.get("status", ""),
                                    "latency_ms": ar.get("latency_ms", 0),
                                }),
                            }

                    elif node_name == "synthesizer":
                        response_text = output.get("final_response", "")
                        if response_text:
                            # Emit response_start + token stream + response_end
                            yield {
                                "event": "response_start",
                                "data": json.dumps({}),
                            }
                            # Stream in chunks for progressive display
                            chunk_size = 80
                            for i in range(0, len(response_text), chunk_size):
                                yield {
                                    "event": "token",
                                    "data": json.dumps({
                                        "text": response_text[i:i + chunk_size],
                                    }),
                                }
                            yield {
                                "event": "response_end",
                                "data": json.dumps({
                                    "full_text": response_text,
                                }),
                            }
                        final_result = output

            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

            # Persist
            turn = final_result.get("turn_number", 0)
            await save_message(
                session_id=conversation_id, turn_number=turn,
                role="user", content=body.query,
            )
            await save_message(
                session_id=conversation_id, turn_number=turn,
                role="assistant",
                content=final_result.get("final_response", ""),
                agents_used=final_result.get("routing_decision", {}).get("agents", []),
                sources=final_result.get("sources", []),
                follow_ups=final_result.get("follow_up_suggestions", []),
                metadata={"total_ms": elapsed_ms},
            )

            # Final result event (matches reference API)
            rd = final_result.get("routing_decision", {})
            yield {
                "event": "result",
                "data": json.dumps({
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "response": final_result.get("final_response", ""),
                    "agent": ",".join(rd.get("agents", [])),
                    "loop_used": "langgraph",
                    "turns_used": final_result.get("turn_number", 0),
                    "duration_ms": elapsed_ms,
                    "tools_called": _extract_tools_called(final_result),
                }),
            }

            yield {"event": "done", "data": "{}"}

            yield {
                "event": "suggestions",
                "data": json.dumps({
                    "suggestions": _build_suggestions(
                        final_result.get("follow_up_suggestions", [])
                    ),
                }),
            }

        except Exception as e:
            logger.exception("Stream error")
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


_TRACKED_NODES = frozenset({
    "intake", "router", "dispatcher",
    "agent_executor", "synthesizer", "evaluator",
})
