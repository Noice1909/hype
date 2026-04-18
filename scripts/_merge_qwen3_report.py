"""Merge partial qwen3 run (S1-S3 + S4 T1-19) with resume run (S4 T20-25 + S5).

Parses both log files and produces a unified 5-session summary identical in
shape to the llama3.1:8b baseline report — so we can compare apples to apples.
"""
import re
from pathlib import Path

PARTIAL_LOG = Path("D:/Project/hype/scripts/comprehensive_run_qwen3.log")
RESUME_LOG = Path("D:/Project/hype/scripts/comprehensive_run_qwen3_resume.log")
OUT = Path("D:/Project/hype/scripts/comprehensive_report_qwen3_merged.txt")


# ── Per-turn parser ──────────────────────────────────────────────────────────

TURN_RE = re.compile(
    r"^  ┌─ T(?P<num>\d+).*?^  └─",
    re.DOTALL | re.MULTILINE,
)

ROUTING_RE = re.compile(r"│ Routing : (?P<agents>[^ ]+).*?drift=(?P<drift>yes|no|YES|no)")
DEEPEVAL_RE = re.compile(
    r"│ DeepEval: faith=(?P<f>[^ ]+)\s+rel=(?P<r>[^ ]+)\s+hallu=(?P<h>[^ ]+)\s+\((?P<eval_ms>\d+)ms\)"
)
LATENCY_RE = re.compile(r"│ Latency : graph=(?P<graph>\d+)ms")
SUGG_RE = re.compile(r"│ Sugg    : (?P<n>\d+) →")
TOOL_RE = re.compile(r"│ Tool    : ")


def _parse_score(s: str):
    if s == "—" or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_turns(text: str) -> list[dict]:
    """Parse all turn cards from a log block."""
    turns = []
    for m in TURN_RE.finditer(text):
        block = m.group(0)
        turn_num = int(m.group("num"))

        rm = ROUTING_RE.search(block)
        agents = rm.group("agents") if rm else ""
        drift = (rm.group("drift").upper() == "YES") if rm else False

        dm = DEEPEVAL_RE.search(block)
        if dm:
            faith = _parse_score(dm.group("f"))
            rel = _parse_score(dm.group("r"))
            hallu = _parse_score(dm.group("h"))
            eval_ms = int(dm.group("eval_ms"))
        else:
            faith = rel = hallu = None
            eval_ms = 0

        lm = LATENCY_RE.search(block)
        graph_ms = int(lm.group("graph")) if lm else 0

        sm = SUGG_RE.search(block)
        sugg = int(sm.group("n")) if sm else 0

        tools = len(TOOL_RE.findall(block))

        turns.append({
            "num": turn_num, "agents": agents, "drift": drift,
            "tools": tools, "sugg": sugg,
            "faith": faith, "rel": rel, "hallu": hallu,
            "graph_ms": graph_ms, "eval_ms": eval_ms,
        })
    return turns


def _split_sessions(text: str) -> dict[str, list[dict]]:
    """Split text into per-session blocks; dedupe by turn number (last wins).

    The live-flush print in the test script can repeat or partially-print turn
    cards, so we keep only the most complete record for each turn number.
    """
    sessions = {}
    session_re = re.compile(r"^  (S\d[^(]+)\(run #\d", re.MULTILINE)
    matches = list(session_re.finditer(text))
    for i, mh in enumerate(matches):
        name = mh.group(1).strip().rstrip(" —")
        start = mh.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parsed = _parse_turns(text[start:end])
        # Dedupe by turn number: prefer the most complete record (most non-zero fields)
        by_num: dict[int, dict] = {}
        for t in parsed:
            existing = by_num.get(t["num"])
            if existing is None:
                by_num[t["num"]] = t
                continue
            # Keep whichever has more populated fields (faith/rel/hallu/agents/sugg)
            score_new = sum(1 for k in ("faith", "rel", "hallu") if t[k] is not None) \
                        + (1 if t["agents"] else 0) + (1 if t["sugg"] else 0)
            score_old = sum(1 for k in ("faith", "rel", "hallu") if existing[k] is not None) \
                        + (1 if existing["agents"] else 0) + (1 if existing["sugg"] else 0)
            if score_new > score_old:
                by_num[t["num"]] = t
        sessions[name] = sorted(by_num.values(), key=lambda x: x["num"])
    return sessions


# ── Per-session summary ──────────────────────────────────────────────────────

def _summary(turns: list[dict]) -> dict:
    if not turns:
        return {
            "n": 0, "drifts": 0, "sug_total": 0,
            "graph_avg": 0, "graph_p50": 0, "graph_p95": 0,
            "graph_min": 0, "graph_max": 0, "eval_avg": 0,
            "faith_avg": None, "rel_avg": None, "hallu_avg": None,
            "faith_n": 0, "rel_n": 0, "hallu_n": 0, "agent_counts": {},
        }
    times = [t["graph_ms"] for t in turns]
    eval_times = [t["eval_ms"] for t in turns if t["eval_ms"]]
    drifts = sum(1 for t in turns if t["drift"])
    sug_total = sum(t["sugg"] for t in turns)
    faith = [t["faith"] for t in turns if t["faith"] is not None]
    rel = [t["rel"] for t in turns if t["rel"] is not None]
    hallu = [t["hallu"] for t in turns if t["hallu"] is not None]
    agent_counts = {}
    for t in turns:
        for a in t["agents"].split(","):
            a = a.strip()
            if a:
                agent_counts[a] = agent_counts.get(a, 0) + 1
    return {
        "n": len(turns), "drifts": drifts, "sug_total": sug_total,
        "graph_avg": sum(times) // len(times) if times else 0,
        "graph_p50": sorted(times)[len(times) // 2] if times else 0,
        "graph_p95": sorted(times)[max(0, int(len(times) * 0.95) - 1)] if times else 0,
        "graph_min": min(times) if times else 0,
        "graph_max": max(times) if times else 0,
        "eval_avg": sum(eval_times) // len(eval_times) if eval_times else 0,
        "faith_avg": sum(faith) / len(faith) if faith else None,
        "rel_avg": sum(rel) / len(rel) if rel else None,
        "hallu_avg": sum(hallu) / len(hallu) if hallu else None,
        "faith_n": len(faith), "rel_n": len(rel), "hallu_n": len(hallu),
        "agent_counts": agent_counts,
    }


def _fmt(s):
    return f"{s:.2f}" if isinstance(s, float) else "—"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    partial = PARTIAL_LOG.read_text(encoding="utf-8", errors="replace")
    resume = RESUME_LOG.read_text(encoding="utf-8", errors="replace")

    partial_sessions = _split_sessions(partial)
    resume_sessions = _split_sessions(resume)

    # Map them into an ordered, cleanly named structure
    s1 = partial_sessions.get("S1 — Broad Enterprise Exploration", []) or \
         next((v for k, v in partial_sessions.items() if "S1" in k), [])
    s2 = next((v for k, v in partial_sessions.items() if "S2" in k), [])
    s3 = next((v for k, v in partial_sessions.items() if "S3" in k), [])
    s4_partial = next((v for k, v in partial_sessions.items() if "S4" in k), [])
    s4_resume = next((v for k, v in resume_sessions.items() if "S4" in k), [])
    s5 = next((v for k, v in resume_sessions.items() if "S5" in k), [])

    # Combine S4: partial T1-19 + resume T1-6 (which are real T20-T25)
    s4 = list(s4_partial)
    for t in s4_resume:
        s4.append({**t, "num": 19 + t["num"]})

    sessions = [
        ("S1 — Broad Enterprise Exploration", s1),
        ("S2 — Deep Dive: Single Domain", s2),
        ("S3 — Multi-Agent + Cross-System", s3),
        ("S4 — Drift-Heavy + Memory", s4),
        ("S5 — Follow-up Chain + Suggestions", s5),
    ]

    out = []
    out.append("=" * 105)
    out.append("  DIVA COMPREHENSIVE SESSION TEST REPORT — qwen3:14b (merged)")
    out.append("  Graph LLM     : ollama / qwen3:14b")
    out.append("  DeepEval LLM  : ollama / qwen3:4b   sample_every=5")
    out.append("  MCP servers   : neo4j (HTTP :3006) + dda-mongodb (HTTP :8080)")
    out.append("  Note          : S1-S3 + S4(T1-T19) from original run that crashed at S4 T19.")
    out.append("                  S4(T20-T25) + S5 from resume run after MCP/services restart.")
    out.append("=" * 105)

    # Per-session summaries
    for name, turns in sessions:
        out.append("")
        out.append("─" * 105)
        out.append(f"  {name}  (n={len(turns)})")
        out.append("─" * 105)
        out.append(f"  {'T#':<3} {'Agents':<22} {'Drift':<5} {'Tools':<5} "
                   f"{'Sugg':<4} {'Faith':<5} {'Rel':<5} {'Hallu':<5} "
                   f"{'Graph':<7} {'Eval':<7}")
        for t in turns:
            d = "YES" if t["drift"] else ""
            out.append(
                f"  {t['num']:<3} {(t['agents'] or 'none')[:20]:<22} {d:<5} "
                f"{t['tools']:<5} {t['sugg']:<4} "
                f"{_fmt(t['faith']):<5} {_fmt(t['rel']):<5} {_fmt(t['hallu']):<5} "
                f"{t['graph_ms']:<7} {t['eval_ms']:<7}"
            )
        s = _summary(turns)
        if not s:
            continue
        out.append("")
        out.append(f"  Turns: {s['n']}  drift={s['drifts']}  sugg={s['sug_total']}")
        out.append(f"  Graph latency: avg={s['graph_avg']}ms  p50={s['graph_p50']}ms  "
                   f"p95={s['graph_p95']}ms  min={s['graph_min']}ms  max={s['graph_max']}ms")
        out.append(f"  Eval  latency: avg={s['eval_avg']}ms")
        out.append(f"  Faithfulness : avg={_fmt(s['faith_avg'])}  (n={s['faith_n']})")
        out.append(f"  Relevancy    : avg={_fmt(s['rel_avg'])}   (n={s['rel_n']})")
        out.append(f"  Hallucination: avg={_fmt(s['hallu_avg'])}  (n={s['hallu_n']})")
        out.append(f"  Agent usage  : " + ", ".join(
            f"{a}={c}" for a, c in sorted(s["agent_counts"].items(), key=lambda x: -x[1])
        ))

    # Grand comparison
    out.append("")
    out.append("=" * 105)
    out.append("  GRAND COMPARISON — ALL 5 SESSIONS (qwen3:14b)")
    out.append("=" * 105)
    out.append(
        f"  {'Session':<36} {'Turns':<6} {'Drift':<6} {'Sugg':<6} "
        f"{'GraphAvg':<10} {'GraphP95':<10} {'EvalAvg':<9} "
        f"{'Faith':<6} {'Rel':<6} {'Hallu':<6}"
    )
    out.append("  " + "─" * 110)
    all_turns = []
    for name, turns in sessions:
        s = _summary(turns)
        if not s:
            continue
        all_turns.extend(turns)
        out.append(
            f"  {name[:34]:<36} {s['n']:<6} {s['drifts']:<6} {s['sug_total']:<6} "
            f"{s['graph_avg']:<10} {s['graph_p95']:<10} {s['eval_avg']:<9} "
            f"{_fmt(s['faith_avg']):<6} {_fmt(s['rel_avg']):<6} {_fmt(s['hallu_avg']):<6}"
        )

    overall = _summary(all_turns)
    out.append("")
    out.append(f"  Total turns      : {overall['n']}")
    out.append(f"  Drift events     : {overall['drifts']} ({overall['drifts']/overall['n']*100:.1f}%)")
    out.append(f"  Graph latency    : avg={overall['graph_avg']}ms  p50={overall['graph_p50']}ms  "
               f"p95={overall['graph_p95']}ms  min={overall['graph_min']}ms  max={overall['graph_max']}ms")
    out.append(f"  Faithfulness avg : {_fmt(overall['faith_avg'])} (n={overall['faith_n']})")
    out.append(f"  Relevancy avg    : {_fmt(overall['rel_avg'])} (n={overall['rel_n']})")
    out.append(f"  Hallucination avg: {_fmt(overall['hallu_avg'])} (n={overall['hallu_n']})")

    out.append("")
    out.append("  Agent usage (all sessions):")
    for a, c in sorted(overall["agent_counts"].items(), key=lambda x: -x[1]):
        out.append(f"    {a}: {c}")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
