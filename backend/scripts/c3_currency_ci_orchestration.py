"""Execute the real C3 orchestrator tests without starting DB-GPT vector runtime.

Only the heavyweight knowledge adapter is substituted during import; the
orchestrator under test is the actual implementation from this branch.
Governed tool calls are monkeypatched per test. This is not a live API gate.
"""
import sys
from types import ModuleType, SimpleNamespace
import pytest

stub = ModuleType("app.services.knowledge")
stub.knowledge_engine = SimpleNamespace()
sys.modules["app.services.knowledge"] = stub

if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", "tests/test_dependent_hybrid_orchestration.py"]))
