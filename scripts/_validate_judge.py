"""Validate all 3 DeepEval metrics work with DivaJudge + qwen3:14b."""
import time
import os

os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_FILE_SYSTEM"] = "READ_ONLY"
os.environ["DEEPEVAL_OLLAMA_MAX_RETRIES"] = "0"
os.environ["DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE"] = "120"
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen3:14b")

from deepeval.metrics import (
    AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric,
)
from deepeval.test_case import LLMTestCase
from diva.evaluation.diva_judge import build_judge

judge = build_judge()
print("judge model:", judge.get_model_name())
print()

tc = LLMTestCase(
    input="What domains exist in our organization?",
    actual_output=(
        "The organization has Cloud Security, Data Platform, and Retail Banking domains. "
        "Cloud Security includes Prisma. Data Platform uses BigQuery. "
        "Retail Banking has the Core Deposit System."
    ),
    retrieval_context=[
        "Cloud Security domain has Prisma Cloud and Wiz applications.",
        "Data Platform domain uses BigQuery and Dataplex.",
        "Retail Banking domain owns the Core Deposit System.",
    ],
    context=[
        "Cloud Security domain has Prisma Cloud and Wiz applications.",
        "Data Platform domain uses BigQuery and Dataplex.",
        "Retail Banking domain owns the Core Deposit System.",
    ],
)

header = f"{'Metric':<25} {'Score':>6}  {'Pass':>5}  {'Time':>7}  Reason"
print(header)
print("-" * 110)
for cls, threshold in [
    (FaithfulnessMetric, 0.7),
    (AnswerRelevancyMetric, 0.7),
    (HallucinationMetric, 0.5),
]:
    m = cls(threshold=threshold, model=judge, async_mode=False)
    t0 = time.time()
    try:
        m.measure(tc)
        passed = "PASS" if m.is_successful() else "FAIL"
        reason = (getattr(m, "reason", "") or "")[:60]
        score = m.score if m.score is not None else float("nan")
        elapsed = time.time() - t0
        print(f"{cls.__name__:<25} {score:>6.2f}  {passed:>5}  {elapsed:>6.1f}s  {reason}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"{cls.__name__:<25} ERROR ({elapsed:>5.1f}s): {str(e)[:80]}")
