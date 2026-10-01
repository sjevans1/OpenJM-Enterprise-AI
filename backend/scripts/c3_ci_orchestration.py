"""Run *real C3 orchestrator* tests without requiring DB-GPT embeddings in CI.

Only the heavyweight RAG adapter import is replaced for this isolated run.
Tests replace tool execution through monkeypatch and exercise real routing,
evidence handling, policy resolution and SQL binding. This is not the live
Gemma/Chroma acceptance and cannot replace it.
"""
import sys
from types import ModuleType, SimpleNamespace

module = ModuleType("app.services.knowledge")
module.knowledge_engine = SimpleNamespace()  # governed tool mocked per test
sys.modules["app.services.knowledge"] = module

import pytest

if __name__ == "__main__":
    raise SystemExit(
        pytest.main(["-q", "tests/test_dependent_hybrid_orchestration.py"])
    )
