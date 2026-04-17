"""DIVA Session A — 20-question comprehensive test.

Tests: Neo4j queries, MongoDB queries, general chat, memory/context retention,
irrelevant questions, drift detection, follow-up suggestions, multi-agent routing.
"""
import asyncio
import os
import sys
import time
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from dotenv import load_dotenv
load_dotenv("D:/Project/hype/.env")

os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b")

import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

sys.path.insert(0, "D:/Project/hype/src")

# Mock MongoDB storage (session persistence)
import diva.storage.mongo as mongo_mod
_mem = {}
async def _noop(*a, **kw): pass
async def _load(sid): return _mem.get(sid)
async def _save(sid, data): _mem[sid] = data
async def _save_msg(*a, **kw): return "m"
mongo_mod.init_mongo = _noop
mongo_mod.close_mongo = _noop
mongo_mod.load_session = _load
mongo_mod.save_session = _save
mongo_mod.save_message = _save_msg
mongo_mod.save_feedback = _noop
mongo_mod.save_eval_result = _noop

from diva.llm.provider import get_llm
from diva.agents.registry import AgentRegistry
from diva.graph.nodes.router import configure_router
from diva.graph.nodes.agent_executor import configure_executor
from diva.graph.nodes.intake import configure_intake
from diva.graph.builder import build_graph
from diva.mcp.client import MCPClientManager

# ── 20 Questions ─────────────────────────────────────────────────────────────

QUESTIONS = [
    # --- Neo4j: Org structure ---
    {"q": "What domains exist in the organization?", "expect": "neo4j", "category": "neo4j-basic"},

    # --- General greeting ---
    {"q": "Hi! What can you help me with?", "expect": "general", "category": "greeting"},

    # --- Neo4j: Deep query ---
    {"q": "Which applications are in the Cloud Security domain and what teams manage them?", "expect": "neo4j", "category": "neo4j-deep"},

    # --- Neo4j: Migration ---
    {"q": "What is the status of the Cloud First migration program? Which waves are completed?", "expect": "neo4j", "category": "neo4j-migration"},

    # --- Memory: Remember something ---
    {"q": "Please remember that I am working on the Wave 2 migration for Retail Banking. My name is Om.", "expect": "memory", "category": "memory-set"},

    # --- MongoDB: Basic ---
    {"q": "Show me all users in the TEST database data collection", "expect": "mongodb", "category": "mongo-basic"},

    # --- Neo4j: Infrastructure ---
    {"q": "Which servers does the Prisma Cloud application run on?", "expect": "neo4j", "category": "neo4j-infra"},

    # --- Irrelevant ---
    {"q": "What is the capital of France?", "expect": "none", "category": "irrelevant"},

    # --- Neo4j: Teams ---
    {"q": "Who leads the Trading Platforms team and how many people are in it?", "expect": "neo4j", "category": "neo4j-team"},

    # --- Context retention test ---
    {"q": "Going back to the Cloud Security domain, what databases do those applications use?", "expect": "neo4j", "category": "context-recall"},

    # --- MongoDB: sample_mflix ---
    {"q": "How many movies are in the sample_mflix database?", "expect": "mongodb", "category": "mongo-count"},

    # --- Drift: Topic change ---
    {"q": "What Autosys batch jobs are scheduled?", "expect": "autosys", "category": "drift-topic-change"},

    # --- Neo4j: Dependencies ---
    {"q": "What applications does the Global Auth Service depend on?", "expect": "neo4j", "category": "neo4j-deps"},

    # --- Memory recall ---
    {"q": "What was my name and what migration wave was I working on?", "expect": "memory", "category": "memory-recall"},

    # --- Multi-agent potential ---
    {"q": "Show me applications in the Data Platform domain and check if there are any users in MongoDB TEST database", "expect": "multi", "category": "multi-agent"},

    # --- Neo4j: Platform ---
    {"q": "What platforms are used in the organization and which vendor provides each?", "expect": "neo4j", "category": "neo4j-platform"},

    # --- Irrelevant 2 ---
    {"q": "Write me a poem about clouds", "expect": "none", "category": "irrelevant-creative"},

    # --- Neo4j: Network ---
    {"q": "What networks exist and which servers are in each network?", "expect": "neo4j", "category": "neo4j-network"},

    # --- MongoDB: Deeper ---
    {"q": "Find movies in sample_mflix that have a rating above 9", "expect": "mongodb", "category": "mongo-query"},

    # --- Wrap up ---
    {"q": "Give me a summary of what we discussed in this session", "expect": "general", "category": "session-summary"},
]


def _build_state(session_id: str, message: str) -> dict:
    return {
        "session_id": session_id, "user_message": message, "turn_number": 0,
        "running_summary": "", "entity_scratchpad": [], "sliding_window": [],
        "drift_detected": False, "previous_topic_summary": "",
        "routing_decision": {"agents": [], "execution_mode": "parallel", "reasoning": "", "sequential_plan": None},
        "agent_results": [], "pending_agents": [],
        "final_response": "", "follow_up_suggestions": [], "sources": [], "eval_payload": {},
    }


async def main():
    print("=" * 80)
    print("  DIVA SESSION A — 20-Question Comprehensive Test")
    print("=" * 80)

    # Setup
    registry = AgentRegistry.from_yaml("D:/Project/hype/configs/agents.yaml")
    mcp = MCPClientManager("D:/Project/hype/configs/mcp_servers.yaml")
    await mcp.startup(server_ids=["neo4j", "mongodb"])

    neo4j_ok = mcp.is_connected("neo4j")
    mongo_ok = mcp.is_connected("mongodb")
    print(f"\n  Neo4j MCP: {'CONNECTED' if neo4j_ok else 'FAILED'}")
    print(f"  MongoDB MCP: {'CONNECTED' if mongo_ok else 'FAILED'}")

    configure_intake("D:/Project/hype/configs/context.yaml")
    configure_router(registry)
    configure_executor(registry=registry, mcp_manager=mcp, llm_factory=get_llm)
    graph = build_graph()

    SESSION_ID = "session-a-test"
    results = []
    total_start = time.perf_counter()

    for i, item in enumerate(QUESTIONS, 1):
        q = item["q"]
        category = item["category"]

        print(f"\n{'─' * 80}")
        print(f"  Q{i:02d} [{category}]: {q}")
        print(f"{'─' * 80}")

        start = time.perf_counter()
        try:
            result = await graph.ainvoke(_build_state(SESSION_ID, q))
            elapsed = (time.perf_counter() - start) * 1000

            rd = result.get("routing_decision", {})
            agents = rd.get("agents", [])
            mode = rd.get("execution_mode", "")
            reasoning = rd.get("reasoning", "")[:100]
            drift = result.get("drift_detected", False)
            response = result.get("final_response", "")
            follow_ups = result.get("follow_up_suggestions", [])
            sources = result.get("sources", [])
            tool_calls = []
            for ar in result.get("agent_results", []):
                for tc in ar.get("tool_calls_made", []):
                    tool_calls.append(f"{tc['tool']}({json.dumps(tc['args'])[:60]})")

            print(f"  Router  → agents={agents} mode={mode}")
            print(f"  Reason  → {reasoning}")
            if drift:
                print(f"  DRIFT DETECTED!")
            if tool_calls:
                print(f"  Tools   → {tool_calls}")
            print(f"  Sources → {sources}")
            print(f"\n  Response ({len(response)} chars):")
            # Show first 400 chars
            resp_lines = response[:400].split("\n")
            for line in resp_lines:
                print(f"    {line}")
            if len(response) > 400:
                print(f"    ... ({len(response) - 400} more chars)")
            if follow_ups:
                print(f"\n  Follow-ups:")
                for fu in follow_ups[:3]:
                    print(f"    → {fu}")
            print(f"\n  Time: {elapsed:.0f}ms")

            results.append({
                "q_num": i,
                "category": category,
                "agents": agents,
                "tool_calls": len(tool_calls),
                "sources": len(sources),
                "response_len": len(response),
                "follow_ups": len(follow_ups),
                "drift": drift,
                "time_ms": round(elapsed),
                "status": "ok",
            })

        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            print(f"  ERROR: {e}")
            results.append({
                "q_num": i, "category": category, "agents": [],
                "tool_calls": 0, "sources": 0, "response_len": 0,
                "follow_ups": 0, "drift": False, "time_ms": round(elapsed),
                "status": f"error: {e}",
            })

    total_elapsed = (time.perf_counter() - total_start) * 1000

    # ── Summary Report ─────────────────────────────────────────────────────
    print(f"\n\n{'=' * 80}")
    print("  SESSION A — PERFORMANCE REPORT")
    print(f"{'=' * 80}\n")

    print(f"  {'Q#':<4} {'Category':<22} {'Agents':<20} {'Tools':<6} {'Sources':<8} {'FU':<4} {'Drift':<6} {'Time':<8} {'Status'}")
    print(f"  {'─'*4} {'─'*22} {'─'*20} {'─'*6} {'─'*8} {'─'*4} {'─'*6} {'─'*8} {'─'*6}")

    for r in results:
        agents_str = ",".join(r["agents"]) if r["agents"] else "none"
        print(
            f"  {r['q_num']:<4} {r['category']:<22} {agents_str:<20} "
            f"{r['tool_calls']:<6} {r['sources']:<8} {r['follow_ups']:<4} "
            f"{'YES' if r['drift'] else '':.<6} {r['time_ms']:<8} {r['status']}"
        )

    # Aggregates
    times = [r["time_ms"] for r in results if r["status"] == "ok"]
    tool_total = sum(r["tool_calls"] for r in results)
    source_total = sum(r["sources"] for r in results)
    drift_count = sum(1 for r in results if r["drift"])
    error_count = sum(1 for r in results if r["status"] != "ok")

    print(f"\n  {'─' * 80}")
    print(f"  Total questions:      {len(QUESTIONS)}")
    print(f"  Successful:           {len(times)}")
    print(f"  Errors:               {error_count}")
    print(f"  Total tool calls:     {tool_total}")
    print(f"  Total sources cited:  {source_total}")
    print(f"  Drift detections:     {drift_count}")
    print(f"  Avg response time:    {sum(times)/len(times):.0f}ms" if times else "  No successful queries")
    print(f"  Min response time:    {min(times):.0f}ms" if times else "")
    print(f"  Max response time:    {max(times):.0f}ms" if times else "")
    print(f"  Total session time:   {total_elapsed/1000:.1f}s")
    print(f"  {'─' * 80}")

    # Agent usage breakdown
    agent_counts = {}
    for r in results:
        for a in r["agents"]:
            agent_counts[a] = agent_counts.get(a, 0) + 1
    print(f"\n  Agent usage:")
    for agent, count in sorted(agent_counts.items(), key=lambda x: -x[1]):
        print(f"    {agent}: {count} queries")

    await mcp.shutdown()
    print(f"\n{'=' * 80}")
    print("  SESSION A COMPLETE")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
