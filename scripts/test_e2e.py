"""End-to-end DIVA test — Neo4j + MongoDB MCP + Ollama."""
import asyncio
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Environment
from dotenv import load_dotenv
load_dotenv("D:/Project/hype/.env")

os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b")

import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

sys.path.insert(0, "D:/Project/hype/src")

# Monkey-patch MongoDB storage (the DIVA storage layer — not the MCP server)
# since local MongoDB isn't running for session persistence
import diva.storage.mongo as mongo_mod
_mem_sessions = {}
async def _noop(*a, **kw): pass
async def _fake_load(sid): return _mem_sessions.get(sid)
async def _fake_save(sid, data): _mem_sessions[sid] = data
async def _fake_save_msg(*a, **kw): return "msg-test"
mongo_mod.init_mongo = _noop
mongo_mod.close_mongo = _noop
mongo_mod.load_session = _fake_load
mongo_mod.save_session = _fake_save
mongo_mod.save_message = _fake_save_msg
mongo_mod.save_feedback = _noop
mongo_mod.save_eval_result = _noop

from diva.llm.provider import get_llm
from diva.agents.registry import AgentRegistry
from diva.graph.nodes.router import configure_router
from diva.graph.nodes.agent_executor import configure_executor
from diva.graph.nodes.intake import configure_intake
from diva.graph.builder import build_graph
from diva.mcp.client import MCPClientManager


def _build_state(session_id: str, message: str) -> dict:
    return {
        "session_id": session_id, "user_message": message, "turn_number": 0,
        "running_summary": "", "entity_scratchpad": [], "sliding_window": [],
        "drift_detected": False, "previous_topic_summary": "",
        "routing_decision": {"agents": [], "execution_mode": "parallel", "reasoning": "", "sequential_plan": None},
        "agent_results": [], "pending_agents": [],
        "final_response": "", "follow_up_suggestions": [], "sources": [], "eval_payload": {},
    }


def _print_result(result: dict, elapsed_ms: float):
    rd = result.get("routing_decision", {})
    print(f"\n  Router: agents={rd.get('agents')} mode={rd.get('execution_mode')}")
    print(f"  Reasoning: {rd.get('reasoning', '')[:120]}")

    for ar in result.get("agent_results", []):
        print(f"\n  Agent [{ar['agent_id']}] status={ar['status']} latency={ar['latency_ms']}ms")
        tools = [tc["tool"] for tc in ar["tool_calls_made"]]
        if tools:
            print(f"    Tools: {tools}")
            for tc in ar["tool_calls_made"][:3]:
                print(f"      {tc['tool']}({tc['args']}) -> {tc['result_preview'][:120]}")
        print(f"    Response: {ar['response_text'][:300]}")

    print(f"\n  --- RESPONSE ---")
    print(f"  {result.get('final_response', '')[:600]}")

    fups = result.get("follow_up_suggestions", [])
    if fups:
        print(f"\n  --- FOLLOW-UPS ---")
        for fu in fups:
            print(f"    - {fu}")

    print(f"\n  Sources: {result.get('sources', [])}")
    print(f"  Total: {elapsed_ms:.0f}ms | Drift: {result.get('drift_detected', False)}")


async def main():
    print("=" * 70)
    print("DIVA END-TO-END TEST — Neo4j + MongoDB MCP + Ollama")
    print("=" * 70)

    # 1. Registry
    registry = AgentRegistry.from_yaml("D:/Project/hype/configs/agents.yaml")
    print(f"\n[1] Registry: {registry.agent_ids}")

    # 2. Start both Neo4j and MongoDB MCP servers
    mcp = MCPClientManager("D:/Project/hype/configs/mcp_servers.yaml")
    print(f"\n[2] Starting MCP servers (neo4j + mongodb)...")
    await mcp.startup(server_ids=["neo4j", "mongodb"])

    for sid in ["neo4j", "mongodb"]:
        connected = mcp.is_connected(sid)
        print(f"    {sid}: {'CONNECTED' if connected else 'FAILED'}")
        if connected:
            tools = await mcp.list_tools(sid)
            print(f"      Tools: {[t.name for t in tools]}")

    # 3. Configure pipeline
    configure_intake("D:/Project/hype/configs/context.yaml")
    configure_router(registry)
    configure_executor(registry=registry, mcp_manager=mcp, llm_factory=get_llm)
    graph = build_graph()
    print(f"\n[3] Graph compiled\n")

    # 4. Run test questions
    questions = [
        "What domains exist in the organization and how many applications are in each?",
    ]

    for i, q in enumerate(questions, 1):
        print(f"{'=' * 70}")
        print(f"Q{i}: {q}")
        print("=" * 70)

        start = time.perf_counter()
        try:
            result = await graph.ainvoke(_build_state(f"test-{i:03d}", q))
            elapsed = (time.perf_counter() - start) * 1000
            _print_result(result, elapsed)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            print(f"\n  ERROR after {elapsed:.0f}ms: {e}")
            import traceback
            traceback.print_exc()

    # Cleanup
    await mcp.shutdown()
    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
