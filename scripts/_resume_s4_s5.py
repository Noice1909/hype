"""Resume qwen3 comprehensive run: S4 turns 20-25 + S5 turns 1-25.

Reuses test_comprehensive_sessions.py's machinery but overrides the session
list to only the remaining turns. Saves to comprehensive_report_qwen3_resume.txt.
"""
import asyncio
import os
import sys

# Hard-fail DeepEval retries — the previous run hung here.
os.environ["DEEPEVAL_OLLAMA_MAX_RETRIES"] = "0"
os.environ["DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE"] = "60"

sys.path.insert(0, "D:/Project/hype/scripts")
import test_comprehensive_sessions as cmp_test

# Original S4 / S5 question sets (must match the original script).
_ORIG_S4 = cmp_test.SESSION_4["questions"]
_ORIG_S5 = cmp_test.SESSION_5["questions"]

# S4 turns 20..25 (1-indexed) = list slice [19:25]
S4_RESUME = {
    "name": "S4-resume — Drift-Heavy + Memory (T20-T25)",
    "questions": _ORIG_S4[19:25],
}

# S5 from the start
S5_FULL = {
    "name": "S5 — Follow-up Chain + Suggestions",
    "questions": _ORIG_S5,
}

cmp_test.ALL_SESSIONS = [S4_RESUME, S5_FULL]

asyncio.run(cmp_test.main())
