"""DIVA Comprehensive Session Tests — 5 sessions x 25 turns with DeepEval.

Captures per-turn:
  - Latency (graph + deepeval)
  - Routing decision (agents, mode, reasoning)
  - Tool calls
  - Drift detection
  - Suggestions (count + agent-tagged shape)
  - DeepEval scores (faithfulness / relevancy / hallucination)

Outputs:
  - Detailed per-turn tables for each session
  - Grand summary comparison across all 5 runs
  - Saved to scripts/comprehensive_report.txt
"""
import asyncio
import os
import sys
import time
import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)

from dotenv import load_dotenv
load_dotenv("D:/Project/hype/.env")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b")
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_FILE_SYSTEM"] = "READ_ONLY"
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "45")
os.environ.setdefault("DEEPEVAL_OLLAMA_MAX_RETRIES", "0")

import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
sys.path.insert(0, "D:/Project/hype/src")

# In-memory session store (replaces MongoDB for the test)
import diva.storage.mongo as m
_S: dict = {}
async def _n(*a, **kw): pass
async def _l(sid): return _S.get(sid)
async def _sv(sid, d): _S[sid] = d
async def _sm(*a, **kw): return "m"
m.init_mongo = _n; m.close_mongo = _n
m.load_session = _l; m.save_session = _sv
m.save_message = _sm; m.save_feedback = _n; m.save_eval_result = _n

from diva.llm.provider import get_llm
from diva.agents.registry import AgentRegistry
from diva.graph.nodes.router import configure_router
from diva.graph.nodes.synthesizer import configure_synthesizer
from diva.graph.nodes.agent_executor import configure_executor
from diva.graph.nodes.intake import configure_intake
from diva.graph.builder import build_graph
from diva.mcp.client import MCPClientManager

# ── DeepEval setup with Ollama (avoids OpenAI key requirement) ───────────────
USE_DEEPEVAL = os.getenv("RUN_DEEPEVAL", "1") == "1"
# Sample DeepEval every Nth turn to keep total runtime sane (default: every 3rd)
DEEPEVAL_SAMPLE_EVERY = int(os.getenv("DEEPEVAL_SAMPLE_EVERY", "3"))
DEEPEVAL_MODEL = os.getenv("DEEPEVAL_MODEL", "phi:latest")
_DEEPEVAL_MODEL = None


def _get_deepeval_model():
    global _DEEPEVAL_MODEL
    if _DEEPEVAL_MODEL is None:
        from deepeval.models.llms.ollama_model import OllamaModel
        _DEEPEVAL_MODEL = OllamaModel(
            model=DEEPEVAL_MODEL,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    return _DEEPEVAL_MODEL


async def _score_turn(user_msg: str, response: str, contexts: list[str], turn_idx: int) -> dict:
    """Run DeepEval metrics synchronously — returns scores or None on failure.

    Sampled: only runs DeepEval on every Nth turn (1-indexed) to keep runtime sane.
    """
    if not USE_DEEPEVAL or not response or not contexts:
        return {"faith": None, "rel": None, "hallu": None, "eval_ms": 0, "evaluated": False}
    if DEEPEVAL_SAMPLE_EVERY > 1 and turn_idx % DEEPEVAL_SAMPLE_EVERY != 0:
        return {"faith": None, "rel": None, "hallu": None, "eval_ms": 0, "evaluated": False}

    start = time.perf_counter()
    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric,
        )
        from deepeval.test_case import LLMTestCase

        model = _get_deepeval_model()
        tc = LLMTestCase(
            input=user_msg, actual_output=response, retrieval_context=contexts,
            context=contexts,  # HallucinationMetric needs `context`
        )
        scores: dict = {}
        for cls, key in [
            (FaithfulnessMetric, "faith"),
            (AnswerRelevancyMetric, "rel"),
            (HallucinationMetric, "hallu"),
        ]:
            try:
                metric = cls(threshold=0.5, model=model, async_mode=False)
                await asyncio.to_thread(metric.measure, tc)
                scores[key] = round(metric.score, 2) if metric.score is not None else None
            except Exception as e:
                scores[key] = None
        scores["eval_ms"] = round((time.perf_counter() - start) * 1000)
        scores["evaluated"] = True
        return scores
    except Exception:
        return {
            "faith": None, "rel": None, "hallu": None,
            "eval_ms": round((time.perf_counter() - start) * 1000),
            "evaluated": False,
        }


# ── 5 Session Definitions × 25 turns each ────────────────────────────────────

SESSION_1 = {
    "name": "S1 — Broad Enterprise Exploration",
    "questions": [
        "Hi! What can DIVA help me with?",
        "What domains exist in our organization?",
        "Show me applications in the Cloud Security domain",
        "Which team manages Prisma Cloud?",
        "What is the migration status for Wave 1?",
        "Remember: my name is Om and I work on Wave 2.",
        "Show me users in the TEST database",
        "What is the capital of France?",
        "Which networks exist in production?",
        "Going back to Cloud Security — what databases do those apps use?",
        "How many movies are in sample_mflix?",
        "What Autosys batch jobs run nightly?",
        "What applications depend on the Global Auth Service?",
        "What was my name and which wave was I working on?",
        "List Data Platform domain applications",
        "What platforms are used and which vendors?",
        "Write a haiku about clouds",
        "What servers are in the production network?",
        "Find movies with rating above 9 in sample_mflix",
        "Who are the leads of each domain?",
        "How are Cloud Security and Data Platform related?",
        "What MongoDB collections exist in the TEST database?",
        "What is the largest application by server count?",
        "Are any apps being decommissioned?",
        "Summarise everything we covered in this session.",
    ],
}

SESSION_2 = {
    "name": "S2 — Deep Dive: Single Domain",
    "questions": [
        "Hello DIVA",
        "Tell me about the Retail Banking domain",
        "What applications are in Retail Banking?",
        "Which teams manage these applications?",
        "What servers do those applications run on?",
        "Are any of these apps being migrated?",
        "What databases does the Core Deposit System use?",
        "Show me the migration waves for Retail Banking",
        "Which wave is the Digital Banking Portal in?",
        "Who are the technical owners of those apps?",
        "What incidents has the Core Deposit System had?",
        "Are any of these apps using legacy infrastructure?",
        "What is the network topology for Retail Banking?",
        "List the dependencies of the Digital Banking Portal",
        "How many users are in the Retail Banking systems?",
        "What batch jobs feed Retail Banking?",
        "Summarise the migration risks for Retail Banking",
        "What documentation exists for the Core Deposit System?",
        "Which Jira epics are open for Retail Banking?",
        "What test coverage do we have for Digital Banking?",
        "Are there any open security findings?",
        "What is the SLA for Core Deposit?",
        "Who would I escalate a Retail Banking incident to?",
        "Compare Retail Banking against Wholesale Banking",
        "Wrap up with the top 3 risks I should know.",
    ],
}

SESSION_3 = {
    "name": "S3 — Multi-Agent + Cross-System",
    "questions": [
        "What is DIVA?",
        "List all domains and their owning business units",
        "How many applications exist organisation-wide?",
        "Show MongoDB collections in TEST and the Neo4j domain count",
        "Which application has the most server dependencies?",
        "Compare Cloud Security and Network Security domains side by side",
        "Find users with status active in MongoDB TEST",
        "What is the relationship between teams and business units?",
        "Who is Alice Chen? Find her across all systems",
        "Remember: the next architecture review is on Friday",
        "Which apps in Cloud Security have open Jira tickets?",
        "What did I ask you to remember earlier?",
        "Show me failed Autosys jobs and the affected apps",
        "Which Confluence pages document the Global Auth Service?",
        "Who committed code to the auth service in the last week?",
        "Find Oracle tables related to PAYMENTS",
        "Which apps write to both Mongo and Oracle?",
        "What data quality issues exist in Dataplex?",
        "Show me tasks assigned to Om in Jira",
        "What is the lineage of the customer_master table?",
        "Are there any Confluence runbooks for migration rollback?",
        "Cross-reference: which apps appear in both Neo4j and Autosys?",
        "What is the latency budget for the auth service?",
        "Search GitHub for recent PRs touching the migration scripts",
        "Summarise the cross-system insights we found.",
    ],
}

SESSION_4 = {
    "name": "S4 — Drift-Heavy + Memory",
    "questions": [
        "Hi DIVA",
        "Tell me about Cloud Security applications",            # topic A
        "Which teams manage those apps?",                        # A continued
        "Switch — show me Autosys batch jobs",                    # DRIFT to B
        "Which jobs failed last night?",                          # B continued
        "Now back to Cloud Security — list the servers",          # DRIFT back to A
        "Remember my name is Sara and I'm a security analyst",   # memory set
        "What movies are in sample_mflix?",                       # DRIFT to C
        "Find movies released after 2020",                        # C continued
        "What was my name?",                                       # memory recall
        "Pivot — what Jira tickets are open for the auth team?", # DRIFT to D
        "Who is assigned the most tickets?",                      # D continued
        "Tell me about the Retail Banking domain",                # DRIFT to E
        "Which databases do those apps use?",                     # E continued
        "What was the Autosys job that failed last night?",       # recall B
        "Switch — show me Confluence runbooks for migrations",   # DRIFT to F
        "Which runbook covers Cloud Security?",                   # F + cross to A
        "What is my role again?",                                  # memory recall
        "Pivot — Oracle tables in PAYMENTS schema",                # DRIFT to G
        "How many rows does PAYMENTS_DAILY have?",                # G continued
        "Back to Cloud Security — how many apps total?",          # DRIFT back to A
        "Tell me a joke",                                          # off-topic
        "What domains had we discussed? List them in order.",     # session recall
        "Forget what I told you earlier",                         # memory clear?
        "What was my name? (Should you remember after forget?)",  # memory test
    ],
}

SESSION_5 = {
    "name": "S5 — Follow-up Chain + Suggestions",
    "questions": [
        "What applications exist in the organisation?",
        # The next questions deliberately test follow-up suggestion quality
        "Pick the first app you mentioned — show me its dependencies",
        "Which team owns it?",
        "What incidents has it had?",
        "Show me servers it runs on",
        "Are any of those servers in a deprecated network?",
        "What apps share servers with this one?",
        "Show me Jira tickets for those shared apps",
        "Who is the assignee of the most recent ticket?",
        "What other apps does that assignee own?",
        "List them with their domains",
        "Which of those apps has open Confluence runbooks?",
        "Show me one runbook's table of contents",
        "Are any sections out of date (>1y)?",
        "Which of those apps writes to Oracle?",
        "Find related Oracle tables",
        "Which Autosys job loads those tables?",
        "Has that job failed in the last 30 days?",
        "If yes, who got paged?",
        "What is the SLA for that job?",
        "Which dashboards monitor it?",
        "Summarise the dependency chain we just walked",
        "What single change would improve resilience here?",
        "Translate that recommendation into a Jira ticket draft",
        "Wrap up — top 5 actions for the team.",
    ],
}

ALL_SESSIONS = [SESSION_1, SESSION_2, SESSION_3, SESSION_4, SESSION_5]


def _state(sid, msg):
    return {
        "session_id": sid, "user_message": msg, "turn_number": 0,
        "cypher_override": None,
        "running_summary": "", "entity_scratchpad": [], "sliding_window": [],
        "drift_detected": False, "previous_topic_summary": "",
        "routing_decision": {
            "agents": [], "execution_mode": "parallel",
            "reasoning": "", "sequential_plan": None,
        },
        "agent_results": [], "pending_agents": [],
        "final_response": "", "follow_up_suggestions": [],
        "sources": [], "eval_payload": {},
    }


# ── Pretty printing helpers ─────────────────────────────────────────────────

def _fmt_score(s):
    return f"{s:.2f}" if isinstance(s, (int, float)) else "—"


def _fmt_list(xs, n=12):
    if not xs:
        return ""
    s = ",".join(map(str, xs))
    return s if len(s) <= n else s[:n - 1] + "…"


def _print_turn_header(buf, q_num, q):
    buf.append(f"\n  ┌─ T{q_num:02d} {'─' * 96}")
    buf.append(f"  │ Q: {q[:100]}")


def _print_turn_footer(buf, t):
    rd_agents = _fmt_list(t["agents"], 22)
    drift = "YES" if t["drift"] else "no"
    sug_summary = ", ".join(
        f"{(s.get('agent') or '?')[:8]}/{s.get('type','?')[:1]}"
        for s in t["suggestions"][:3]
    )
    buf.append(f"  │ Routing : {rd_agents}  mode={t['mode']}  drift={drift}")
    if t["reasoning"]:
        buf.append(f"  │ Reason  : {t['reasoning'][:110]}")
    if t["tool_calls"]:
        for tc in t["tool_calls"][:4]:
            buf.append(f"  │ Tool    : [{tc['agent']}] {tc['tool']}({json.dumps(tc['args'])[:70]})")
    buf.append(f"  │ Resp    : {len(t['response'])} chars  ·  sources={len(t['sources'])}")
    resp_preview = t["response"][:160].replace("\n", " ")
    buf.append(f"  │           {resp_preview}")
    if t["suggestions"]:
        buf.append(f"  │ Sugg    : {len(t['suggestions'])} → {sug_summary}")
        for s in t["suggestions"][:3]:
            agent = s.get("agent", "?")
            stype = s.get("type", "?")
            txt = s.get("text", "")[:90]
            buf.append(f"  │           [{agent:>10}|{stype:<7}] {txt}")
    buf.append(
        f"  │ DeepEval: faith={_fmt_score(t['faith'])}  "
        f"rel={_fmt_score(t['rel'])}  "
        f"hallu={_fmt_score(t['hallu'])}  "
        f"({t['eval_ms']}ms)"
    )
    buf.append(f"  │ Latency : graph={t['graph_ms']}ms  total={t['graph_ms'] + t['eval_ms']}ms")
    buf.append(f"  └{'─' * 100}")


# ── Main test loop ──────────────────────────────────────────────────────────

async def run_session(graph, session_def, run_idx, buf):
    name = session_def["name"]
    questions = session_def["questions"]
    session_id = f"comp-run{run_idx}-{name[:6].lower().replace(' ', '-')}"

    buf.append(f"\n{'=' * 105}")
    buf.append(f"  {name}  (run #{run_idx}, session_id={session_id})")
    buf.append(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    buf.append(f"{'=' * 105}")

    turns = []
    sess_start = time.perf_counter()

    for i, q in enumerate(questions, 1):
        graph_start = time.perf_counter()
        try:
            result = await graph.ainvoke(_state(session_id, q))
            graph_ms = round((time.perf_counter() - graph_start) * 1000)

            rd = result.get("routing_decision", {}) or {}
            agents = rd.get("agents", []) or []
            mode = rd.get("execution_mode", "")
            reasoning = (rd.get("reasoning", "") or "")[:200]
            drift = bool(result.get("drift_detected", False))
            response = result.get("final_response", "") or ""
            suggestions = result.get("follow_up_suggestions", []) or []
            sources = result.get("sources", []) or []

            tool_calls = []
            agent_responses = []
            for ar in result.get("agent_results", []):
                if ar.get("status") == "success" and ar.get("response_text"):
                    agent_responses.append(ar["response_text"])
                for tc in ar.get("tool_calls_made", []):
                    tool_calls.append({
                        "agent": ar.get("agent_id", ""),
                        "tool": tc.get("tool", ""),
                        "args": tc.get("args", {}),
                    })

            scores = await _score_turn(q, response, agent_responses, i)

            t = {
                "q_num": i, "q": q,
                "agents": agents, "mode": mode, "reasoning": reasoning,
                "drift": drift, "response": response,
                "suggestions": suggestions, "sources": sources,
                "tool_calls": tool_calls,
                "graph_ms": graph_ms,
                "eval_ms": scores["eval_ms"],
                "faith": scores["faith"], "rel": scores["rel"], "hallu": scores["hallu"],
                "status": "ok",
            }
        except Exception as e:
            graph_ms = round((time.perf_counter() - graph_start) * 1000)
            t = {
                "q_num": i, "q": q, "agents": [], "mode": "", "reasoning": "",
                "drift": False, "response": f"ERROR: {e}", "suggestions": [],
                "sources": [], "tool_calls": [],
                "graph_ms": graph_ms, "eval_ms": 0,
                "faith": None, "rel": None, "hallu": None,
                "status": "error",
            }

        turns.append(t)
        _print_turn_header(buf, i, q)
        _print_turn_footer(buf, t)
        # Flush after each turn so progress is visible if interrupted
        print("\n".join(buf[-15:]))

    sess_secs = round(time.perf_counter() - sess_start, 1)

    # ── Per-session summary table ──
    buf.append(f"\n  {'─' * 100}")
    buf.append(f"  SESSION SUMMARY — {name}")
    buf.append(f"  {'─' * 100}")
    header = (
        f"  {'T#':<3} {'Agents':<24} {'Drift':<5} {'Tools':<5} "
        f"{'Sugg':<4} {'Faith':<5} {'Rel':<5} {'Hallu':<5} "
        f"{'Graph':<7} {'Eval':<6} {'Total':<7} {'Status'}"
    )
    buf.append(header)
    buf.append("  " + "─" * len(header))
    for t in turns:
        agents_str = ",".join(t["agents"])[:22] if t["agents"] else "none"
        d = "YES" if t["drift"] else ""
        buf.append(
            f"  {t['q_num']:<3} {agents_str:<24} {d:<5} "
            f"{len(t['tool_calls']):<5} {len(t['suggestions']):<4} "
            f"{_fmt_score(t['faith']):<5} {_fmt_score(t['rel']):<5} "
            f"{_fmt_score(t['hallu']):<5} "
            f"{t['graph_ms']:<7} {t['eval_ms']:<6} "
            f"{t['graph_ms'] + t['eval_ms']:<7} {t['status']}"
        )

    ok = [t for t in turns if t["status"] == "ok"]
    times = [t["graph_ms"] for t in ok]
    eval_times = [t["eval_ms"] for t in ok if t["eval_ms"]]
    drifts = sum(1 for t in turns if t["drift"])
    sug_total = sum(len(t["suggestions"]) for t in turns)
    sug_with_agent = sum(
        1 for t in turns for s in t["suggestions"] if s.get("agent")
    )
    faith = [t["faith"] for t in turns if t["faith"] is not None]
    rel = [t["rel"] for t in turns if t["rel"] is not None]
    hallu = [t["hallu"] for t in turns if t["hallu"] is not None]

    agent_counts = {}
    for t in turns:
        for a in t["agents"]:
            agent_counts[a] = agent_counts.get(a, 0) + 1

    buf.append("")
    buf.append(f"  Turns         : {len(turns)} (ok={len(ok)}, err={len(turns) - len(ok)})")
    buf.append(f"  Drift events  : {drifts}")
    buf.append(f"  Suggestions   : {sug_total} total, {sug_with_agent} agent-tagged "
               f"({(sug_with_agent / sug_total * 100) if sug_total else 0:.0f}%)")
    if times:
        buf.append(f"  Graph latency : avg={sum(times) // len(times)}ms  "
                   f"min={min(times)}ms  max={max(times)}ms  "
                   f"p50={sorted(times)[len(times)//2]}ms  "
                   f"p95={sorted(times)[max(0, int(len(times)*0.95) - 1)]}ms")
    if eval_times:
        buf.append(f"  Eval latency  : avg={sum(eval_times) // len(eval_times)}ms")
    if faith:
        buf.append(f"  Faithfulness  : avg={sum(faith) / len(faith):.2f}  "
                   f"min={min(faith):.2f}  max={max(faith):.2f}  (n={len(faith)})")
    if rel:
        buf.append(f"  Relevancy     : avg={sum(rel) / len(rel):.2f}  "
                   f"min={min(rel):.2f}  max={max(rel):.2f}  (n={len(rel)})")
    if hallu:
        buf.append(f"  Hallucination : avg={sum(hallu) / len(hallu):.2f}  "
                   f"min={min(hallu):.2f}  max={max(hallu):.2f}  (n={len(hallu)})")
    buf.append(f"  Wall time     : {sess_secs}s")
    buf.append(f"  Agent usage   :")
    for a, c in sorted(agent_counts.items(), key=lambda x: -x[1]):
        buf.append(f"      {a}: {c}")

    return {
        "name": name, "turns": turns, "wall_secs": sess_secs,
        "ok_count": len(ok), "drift_count": drifts,
        "graph_avg_ms": (sum(times) // len(times)) if times else 0,
        "graph_p95_ms": (sorted(times)[max(0, int(len(times)*0.95) - 1)]) if times else 0,
        "eval_avg_ms": (sum(eval_times) // len(eval_times)) if eval_times else 0,
        "faith_avg": (sum(faith) / len(faith)) if faith else None,
        "rel_avg": (sum(rel) / len(rel)) if rel else None,
        "hallu_avg": (sum(hallu) / len(hallu)) if hallu else None,
        "sug_total": sug_total, "sug_tagged": sug_with_agent,
        "agent_counts": agent_counts,
    }


async def main():
    registry = AgentRegistry.from_yaml("D:/Project/hype/configs/agents.yaml")
    mcp = MCPClientManager("D:/Project/hype/configs/mcp_servers.yaml")
    # Configurable via env: MCP_SERVERS=neo4j,dda-mongodb (comma-sep). Failures non-fatal.
    server_ids = [s.strip() for s in os.getenv("MCP_SERVERS", "neo4j,dda-mongodb").split(",") if s.strip()]
    await mcp.startup(server_ids=server_ids)

    configure_intake("D:/Project/hype/configs/context.yaml")
    configure_router(registry)
    configure_synthesizer(registry)
    configure_executor(registry=registry, mcp_manager=mcp, llm_factory=get_llm)
    graph = build_graph()

    buf = []
    buf.append("=" * 105)
    buf.append("  DIVA COMPREHENSIVE SESSION TEST REPORT")
    buf.append(f"  Generated     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    buf.append(f"  LLM provider  : {os.getenv('LLM_PROVIDER')} / {os.getenv('OLLAMA_MODEL', '')}")
    buf.append(f"  DeepEval      : {'ON' if USE_DEEPEVAL else 'OFF'}  "
               f"model={DEEPEVAL_MODEL}  sample_every={DEEPEVAL_SAMPLE_EVERY}")
    mcp_status = "  ".join(f"{sid}={mcp.is_connected(sid)}" for sid in server_ids)
    buf.append(f"  MCP servers   : {mcp_status}")
    buf.append("=" * 105)

    summaries = []
    grand_start = time.perf_counter()
    for idx, sess in enumerate(ALL_SESSIONS, 1):
        _S.clear()  # fresh memory per session
        summary = await run_session(graph, sess, idx, buf)
        summaries.append(summary)

    grand_secs = round(time.perf_counter() - grand_start, 1)

    # ── Grand cross-session comparison table ──
    buf.append(f"\n\n{'=' * 105}")
    buf.append("  GRAND COMPARISON — ALL 5 SESSIONS")
    buf.append(f"{'=' * 105}")

    header = (
        f"  {'Session':<32} {'Turns':<6} {'OK':<4} {'Drift':<6} "
        f"{'Sugg':<8} {'Tagged':<7} "
        f"{'GraphAvg':<9} {'GraphP95':<9} {'EvalAvg':<8} "
        f"{'Faith':<6} {'Rel':<6} {'Hallu':<6} {'Wall(s)'}"
    )
    buf.append(header)
    buf.append("  " + "─" * (len(header) + 2))
    for s in summaries:
        sug_pct = f"{(s['sug_tagged'] / s['sug_total'] * 100) if s['sug_total'] else 0:.0f}%"
        buf.append(
            f"  {s['name'][:30]:<32} {len(s['turns']):<6} {s['ok_count']:<4} "
            f"{s['drift_count']:<6} {s['sug_total']:<8} {sug_pct:<7} "
            f"{s['graph_avg_ms']:<9} {s['graph_p95_ms']:<9} {s['eval_avg_ms']:<8} "
            f"{_fmt_score(s['faith_avg']):<6} {_fmt_score(s['rel_avg']):<6} "
            f"{_fmt_score(s['hallu_avg']):<6} {s['wall_secs']:<7}"
        )

    # Aggregate across all turns
    all_turns = [t for s in summaries for t in s["turns"]]
    ok_all = [t for t in all_turns if t["status"] == "ok"]
    times_all = [t["graph_ms"] for t in ok_all]
    faith_all = [t["faith"] for t in all_turns if t["faith"] is not None]
    rel_all = [t["rel"] for t in all_turns if t["rel"] is not None]
    hallu_all = [t["hallu"] for t in all_turns if t["hallu"] is not None]
    drift_all = sum(1 for t in all_turns if t["drift"])

    buf.append(f"\n  Total turns      : {len(all_turns)}  (ok={len(ok_all)}, "
               f"err={len(all_turns) - len(ok_all)})")
    buf.append(f"  Drift events     : {drift_all} ({drift_all / len(all_turns) * 100:.1f}%)")
    if times_all:
        srt = sorted(times_all)
        buf.append(f"  Graph latency    : avg={sum(times_all) // len(times_all)}ms  "
                   f"p50={srt[len(srt)//2]}ms  "
                   f"p95={srt[max(0, int(len(srt)*0.95) - 1)]}ms  "
                   f"min={min(srt)}ms  max={max(srt)}ms")
    if faith_all:
        buf.append(f"  Faithfulness avg : {sum(faith_all) / len(faith_all):.2f} "
                   f"(target ≥0.70, n={len(faith_all)})")
    if rel_all:
        buf.append(f"  Relevancy avg    : {sum(rel_all) / len(rel_all):.2f} "
                   f"(target ≥0.70, n={len(rel_all)})")
    if hallu_all:
        buf.append(f"  Hallucination avg: {sum(hallu_all) / len(hallu_all):.2f} "
                   f"(target ≤0.50, n={len(hallu_all)})")
    buf.append(f"  Total wall time  : {grand_secs}s ({grand_secs / 60:.1f}m)")

    # Agent usage across all sessions
    agent_totals: dict = {}
    for s in summaries:
        for a, c in s["agent_counts"].items():
            agent_totals[a] = agent_totals.get(a, 0) + c
    buf.append(f"\n  Agent usage (all sessions):")
    for a, c in sorted(agent_totals.items(), key=lambda x: -x[1]):
        buf.append(f"    {a}: {c}")

    # Verdict
    buf.append(f"\n  {'─' * 100}")
    buf.append("  VERDICT")
    buf.append(f"  {'─' * 100}")
    if faith_all and rel_all and hallu_all:
        f_ok = (sum(faith_all) / len(faith_all)) >= 0.70
        r_ok = (sum(rel_all) / len(rel_all)) >= 0.70
        h_ok = (sum(hallu_all) / len(hallu_all)) <= 0.50
        buf.append(f"  Faithfulness ≥0.70 : {'PASS' if f_ok else 'FAIL'}")
        buf.append(f"  Relevancy    ≥0.70 : {'PASS' if r_ok else 'FAIL'}")
        buf.append(f"  Hallucination ≤0.50: {'PASS' if h_ok else 'FAIL'}")
    err_rate = (len(all_turns) - len(ok_all)) / len(all_turns) * 100
    buf.append(f"  Error rate         : {err_rate:.1f}%")
    if times_all:
        srt = sorted(times_all)
        p95 = srt[max(0, int(len(srt)*0.95) - 1)]
        buf.append(f"  P95 latency        : {p95}ms ({'PASS' if p95 < 30000 else 'WATCH'} vs 30s target)")
    buf.append(f"  {'─' * 100}")

    await mcp.shutdown()

    buf.append(f"\n{'=' * 105}")
    buf.append("  REPORT COMPLETE")
    buf.append(f"{'=' * 105}")

    text = "\n".join(buf)
    out = os.getenv(
        "COMPREHENSIVE_REPORT_PATH",
        "D:/Project/hype/scripts/comprehensive_report.txt",
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n\n=== FINAL REPORT (also saved to {out}) ===\n")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
