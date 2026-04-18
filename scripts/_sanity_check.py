"""Two-turn sanity check for the comprehensive test runner."""
import asyncio
import sys

sys.path.insert(0, "D:/Project/hype/scripts")
import test_comprehensive_sessions as cmp_test

# Override sessions list with one tiny session
cmp_test.ALL_SESSIONS = [{
    "name": "SANITY",
    "questions": [
        "What domains exist in our organization?",
        "Which apps are in the Cloud Security domain?",
    ],
}]

asyncio.run(cmp_test.main())
