"""DIVA Detailed Session Tests — Multiple runs with full per-turn reporting.

Produces detailed reports with:
- Per-turn latency, routing, tool calls with parameters
- LLM response text, generated suggestions
- DeepEval scores (if available)
- Aggregated stats per session run
"""
import asyncio
import os
import sys
import time
import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv("D:/Project/hype/.env")
os.environ.setdefault("LLM_PROVIDER", "ollama")
import certifi; os.environ["SSL_CERT_FILE"] = certifi.where()
sys.path.insert(0, "D:/Project/hype/src")

# Mock MongoDB storage
import diva.storage.mongo as m
_s = {}
async def _n(*a,**kw): pass
async def _l(sid): return _s.get(sid)
async def _sv(sid, d): _s[sid] = d
async def _sm(*a,**kw): return "m"
m.init_mongo=_n; m.close_mongo=_n; m.load_session=_l; m.save_session=_sv
m.save_message=_sm; m.save_feedback=_n; m.save_eval_result=_n

from diva.llm.provider import get_llm
from diva.agents.registry import AgentRegistry
from diva.graph.nodes.router import configure_router
from diva.graph.nodes.agent_executor import configure_executor
from diva.graph.nodes.intake import configure_intake
from diva.graph.builder import build_graph
from diva.mcp.client import MCPClientManager

# ── Session Definitions ──────────────────────────────────────────────────────

SESSION_A = {
    "name": "Session A — Broad Enterprise Exploration",
    "questions": [
        "Hi! What can you help me with?",
        "What domains exist in the organization?",
        "Which applications are in the Cloud Security domain and what teams manage them?",
        "What is the status of the Cloud First migration program?",
        "Please remember that my name is Om and I work on Wave 2 migration.",
        "Show me all users in the TEST database data collection",
        "Which servers does the Prisma Cloud application run on?",
        "What is the capital of France?",
        "Who leads the Trading Platforms team and how many people are in it?",
        "Going back to Cloud Security, what databases do those applications use?",
        "How many movies are in the sample_mflix database?",
        "What Autosys batch jobs are scheduled?",
        "What applications does the Global Auth Service depend on?",
        "What was my name and what migration wave was I working on?",
        "Show me applications in the Data Platform domain and check if there are any users in MongoDB TEST database",
        "What platforms are used in the organization and which vendor provides each?",
        "Write me a poem about data",
        "What networks exist and which servers are in each network?",
        "Find movies in sample_mflix that have a rating above 9",
        "Give me a summary of what we discussed in this session",
    ],
}

SESSION_B = {
    "name": "Session B — Deep Dive Single Domain",
    "questions": [
        "Hello DIVA",
        "Tell me everything about the Retail Banking domain",
        "What applications are in Retail Banking?",
        "Which teams manage those applications?",
        "What servers do the Retail Banking apps run on?",
        "Are any of those apps being migrated? To where?",
        "What databases does the Core Deposit System use?",
        "Show me the migration waves timeline",
        "Which wave is the Digital Banking Portal in?",
        "Thanks for the info!",
    ],
}

SESSION_C = {
    "name": "Session C — Multi-Agent + Edge Cases",
    "questions": [
        "What is DIVA?",
        "List all business units",
        "How many applications are there in total?",
        "Show me MongoDB collections in the TEST database",
        "Which application has the most server dependencies?",
        "2 + 2 = ?",
        "Compare Cloud Security and Network Security domains",
        "Find users with status active in MongoDB TEST",
        "What is the relationship between teams and business units?",
        "Who is Alice Chen?",
        "Remember: the next review meeting is on Friday",
        "What did I ask you to remember?",
    ],
}

ALL_SESSIONS = [SESSION_A, SESSION_B, SESSION_C]


def _state(sid, msg):
    return {
        "session_id": sid, "user_message": msg, "turn_number": 0,
        "running_summary": "", "entity_scratchpad": [], "sliding_window": [],
        "drift_detected": False, "previous_topic_summary": "",
        "routing_decision": {"agents": [], "execution_mode": "parallel", "reasoning": "", "sequential_plan": None},
        "agent_results": [], "pending_agents": [],
        "final_response": "", "follow_up_suggestions": [], "sources": [], "eval_payload": {},
    }


async def run_session(graph, session_def, run_number, report_lines):
    """Run one session and collect detailed per-turn data."""
    name = session_def["name"]
    questions = session_def["questions"]
    session_id = f"run{run_number}-{name[:10].lower().replace(' ','-')}"

    report_lines.append(f"\n{'='*100}")
    report_lines.append(f"  {name} — Run #{run_number}")
    report_lines.append(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"{'='*100}\n")

    turns = []
    total_start = time.perf_counter()

    for i, q in enumerate(questions, 1):
        start = time.perf_counter()
        try:
            result = await graph.ainvoke(_state(session_id, q))
            elapsed = (time.perf_counter() - start) * 1000

            rd = result.get("routing_decision", {})
            agents = rd.get("agents", [])
            reasoning = rd.get("reasoning", "")
            drift = result.get("drift_detected", False)
            response = result.get("final_response", "")
            follow_ups = result.get("follow_up_suggestions", [])
            sources = result.get("sources", [])

            tool_calls = []
            for ar in result.get("agent_results", []):
                for tc in ar.get("tool_calls_made", []):
                    tool_calls.append({
                        "agent": ar.get("agent_id", ""),
                        "tool": tc.get("tool", ""),
                        "args": tc.get("args", {}),
                        "result_preview": tc.get("result_preview", "")[:100],
                    })

            turn = {
                "q_num": i,
                "question": q,
                "agents": agents,
                "reasoning": reasoning[:120],
                "drift": drift,
                "tool_calls": tool_calls,
                "sources": sources,
                "response": response,
                "follow_ups": follow_ups,
                "time_ms": round(elapsed),
                "status": "ok",
            }
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            turn = {
                "q_num": i, "question": q, "agents": [], "reasoning": "",
                "drift": False, "tool_calls": [], "sources": [],
                "response": f"ERROR: {e}", "follow_ups": [],
                "time_ms": round(elapsed), "status": f"error",
            }

        turns.append(turn)

        # Print per-turn detail
        report_lines.append(f"  ┌─ Q{i:02d} {'─'*90}")
        report_lines.append(f"  │ Question:  {q}")
        report_lines.append(f"  │ Router:    agents={turn['agents']}  mode={rd.get('execution_mode','')}")
        report_lines.append(f"  │ Reasoning: {turn['reasoning']}")
        if turn['drift']:
            report_lines.append(f"  │ *** DRIFT DETECTED ***")
        report_lines.append(f"  │ Latency:   {turn['time_ms']}ms")

        if turn['tool_calls']:
            report_lines.append(f"  │ Tool Calls ({len(turn['tool_calls'])}):")
            for tc in turn['tool_calls']:
                args_str = json.dumps(tc['args'])[:80]
                report_lines.append(f"  │   [{tc['agent']}] {tc['tool']}({args_str})")
                report_lines.append(f"  │     → {tc['result_preview']}")

        report_lines.append(f"  │ Sources:   {turn['sources']}")

        # Response (truncated)
        resp_preview = turn['response'][:300].replace('\n', '\n  │            ')
        report_lines.append(f"  │ Response:  {resp_preview}")
        if len(turn['response']) > 300:
            report_lines.append(f"  │            ... ({len(turn['response'])-300} more chars)")

        if turn['follow_ups']:
            report_lines.append(f"  │ Suggestions:")
            for fu in turn['follow_ups'][:3]:
                report_lines.append(f"  │   → {fu}")

        report_lines.append(f"  └{'─'*95}\n")

    total_elapsed = (time.perf_counter() - total_start)

    # ── Session Summary ──
    times = [t["time_ms"] for t in turns if t["status"] == "ok"]
    total_tools = sum(len(t["tool_calls"]) for t in turns)
    total_sources = sum(len(t["sources"]) for t in turns)
    drift_count = sum(1 for t in turns if t["drift"])
    error_count = sum(1 for t in turns if t["status"] != "ok")

    agent_counts = {}
    for t in turns:
        for a in t["agents"]:
            agent_counts[a] = agent_counts.get(a, 0) + 1

    report_lines.append(f"\n  {'─'*95}")
    report_lines.append(f"  SUMMARY — {name} Run #{run_number}")
    report_lines.append(f"  {'─'*95}")

    # Summary table
    header = f"  {'Q#':<4} {'Agents':<22} {'Tools':<6} {'Srcs':<6} {'FU':<4} {'Drift':<6} {'Time(ms)':<10} {'Status'}"
    report_lines.append(header)
    report_lines.append(f"  {'─'*4} {'─'*22} {'─'*6} {'─'*6} {'─'*4} {'─'*6} {'─'*10} {'─'*6}")
    for t in turns:
        agents_str = ",".join(t["agents"])[:20] if t["agents"] else "none"
        d = "YES" if t["drift"] else ""
        report_lines.append(
            f"  {t['q_num']:<4} {agents_str:<22} {len(t['tool_calls']):<6} "
            f"{len(t['sources']):<6} {len(t['follow_ups']):<4} {d:<6} "
            f"{t['time_ms']:<10} {t['status']}"
        )

    report_lines.append(f"\n  Totals:")
    report_lines.append(f"    Questions:       {len(questions)}")
    report_lines.append(f"    Successful:      {len(times)}")
    report_lines.append(f"    Errors:          {error_count}")
    report_lines.append(f"    Tool calls:      {total_tools}")
    report_lines.append(f"    Sources cited:   {total_sources}")
    report_lines.append(f"    Drift detected:  {drift_count}")
    if times:
        report_lines.append(f"    Avg latency:     {sum(times)//len(times)}ms")
        report_lines.append(f"    Min latency:     {min(times)}ms")
        report_lines.append(f"    Max latency:     {max(times)}ms")
    report_lines.append(f"    Total time:      {total_elapsed:.1f}s")
    report_lines.append(f"    Agent usage:")
    for agent, count in sorted(agent_counts.items(), key=lambda x: -x[1]):
        report_lines.append(f"      {agent}: {count}")

    return turns


async def main():
    # Setup
    registry = AgentRegistry.from_yaml("D:/Project/hype/configs/agents.yaml")
    mcp = MCPClientManager("D:/Project/hype/configs/mcp_servers.yaml")
    await mcp.startup(server_ids=["neo4j", "mongodb"])
    configure_intake("D:/Project/hype/configs/context.yaml")
    configure_router(registry)
    configure_executor(registry=registry, mcp_manager=mcp, llm_factory=get_llm)
    graph = build_graph()

    neo4j_ok = mcp.is_connected("neo4j")
    mongo_ok = mcp.is_connected("mongodb")

    report_lines = []
    report_lines.append("="*100)
    report_lines.append("  DIVA DETAILED SESSION TEST REPORT")
    report_lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"  Model: {os.getenv('OLLAMA_MODEL', 'unknown')}")
    report_lines.append(f"  Neo4j MCP: {'CONNECTED' if neo4j_ok else 'DISCONNECTED'}")
    report_lines.append(f"  MongoDB MCP: {'CONNECTED' if mongo_ok else 'DISCONNECTED'}")
    report_lines.append("="*100)

    all_turns = []

    # Run each session
    for session_def in ALL_SESSIONS:
        # Clear session memory between sessions
        _s.clear()
        turns = await run_session(graph, session_def, 1, report_lines)
        all_turns.extend(turns)

    # Run Session A again (different order — reversed)
    _s.clear()
    reversed_session = {
        "name": "Session A-Rev — Reversed Order",
        "questions": list(reversed(SESSION_A["questions"])),
    }
    turns = await run_session(graph, reversed_session, 2, report_lines)
    all_turns.extend(turns)

    # ── Grand Summary ──
    report_lines.append(f"\n\n{'='*100}")
    report_lines.append("  GRAND SUMMARY — ALL SESSIONS")
    report_lines.append(f"{'='*100}\n")

    ok_turns = [t for t in all_turns if t["status"] == "ok"]
    times = [t["time_ms"] for t in ok_turns]
    total_tools = sum(len(t["tool_calls"]) for t in all_turns)

    report_lines.append(f"  Total turns:       {len(all_turns)}")
    report_lines.append(f"  Successful:        {len(ok_turns)}")
    report_lines.append(f"  Errors:            {len(all_turns) - len(ok_turns)}")
    report_lines.append(f"  Total tool calls:  {total_tools}")
    report_lines.append(f"  Total drift:       {sum(1 for t in all_turns if t['drift'])}")
    if times:
        report_lines.append(f"  Avg latency:       {sum(times)//len(times)}ms")
        report_lines.append(f"  P50 latency:       {sorted(times)[len(times)//2]}ms")
        report_lines.append(f"  P95 latency:       {sorted(times)[int(len(times)*0.95)]}ms")
        report_lines.append(f"  Min latency:       {min(times)}ms")
        report_lines.append(f"  Max latency:       {max(times)}ms")

    # Agent usage across all runs
    agent_totals = {}
    for t in all_turns:
        for a in t["agents"]:
            agent_totals[a] = agent_totals.get(a, 0) + 1
    report_lines.append(f"\n  Agent usage (all runs):")
    for agent, count in sorted(agent_totals.items(), key=lambda x: -x[1]):
        report_lines.append(f"    {agent}: {count}")

    await mcp.shutdown()

    report_lines.append(f"\n{'='*100}")
    report_lines.append("  REPORT COMPLETE")
    report_lines.append(f"{'='*100}")

    # Write report to file
    report_text = "\n".join(report_lines)
    report_path = "D:/Project/hype/scripts/session_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Also print to stdout
    print(report_text)
    print(f"\n\nReport saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
