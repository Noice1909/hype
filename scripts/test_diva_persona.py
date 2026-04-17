"""Quick test: DIVA persona for greetings, irrelevant, memory, summary."""
import asyncio, os, sys, time, logging

logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv("D:/Project/hype/.env")
os.environ.setdefault("LLM_PROVIDER", "ollama")
import certifi; os.environ["SSL_CERT_FILE"] = certifi.where()
sys.path.insert(0, "D:/Project/hype/src")

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

TESTS = [
    "Hi there! What can you help me with?",
    "What is the capital of France?",
    "Please remember that my name is Om and I work on Wave 2 migration.",
    "Write me a short poem about data.",
    "What was my name again?",
    "Which applications are in the Cloud Security domain?",  # should NOT go to diva
]

def _state(sid, msg):
    return {"session_id": sid, "user_message": msg, "turn_number": 0,
            "running_summary": "", "entity_scratchpad": [], "sliding_window": [],
            "drift_detected": False, "previous_topic_summary": "",
            "routing_decision": {"agents": [], "execution_mode": "parallel", "reasoning": "", "sequential_plan": None},
            "agent_results": [], "pending_agents": [],
            "final_response": "", "follow_up_suggestions": [], "sources": [], "eval_payload": {}}

async def main():
    reg = AgentRegistry.from_yaml("D:/Project/hype/configs/agents.yaml")
    mcp = MCPClientManager("D:/Project/hype/configs/mcp_servers.yaml")
    await mcp.startup(server_ids=["neo4j", "mongodb"])
    configure_intake("D:/Project/hype/configs/context.yaml")
    configure_router(reg)
    configure_executor(registry=reg, mcp_manager=mcp, llm_factory=get_llm)
    graph = build_graph()

    for i, q in enumerate(TESTS, 1):
        print(f"\n{'='*70}")
        print(f"Q{i}: {q}")
        print(f"{'='*70}")
        t = time.perf_counter()
        r = await graph.ainvoke(_state("persona-test", q))
        ms = (time.perf_counter() - t) * 1000
        rd = r.get("routing_decision", {})
        print(f"  Router: {rd.get('agents')} | {rd.get('reasoning','')[:80]}")
        print(f"  Response: {r.get('final_response','')[:400]}")
        fups = r.get("follow_up_suggestions", [])
        if fups:
            print(f"  Follow-ups: {fups[:2]}")
        print(f"  Time: {ms:.0f}ms")

    await mcp.shutdown()
    print(f"\n{'='*70}\nDONE")

asyncio.run(main())
