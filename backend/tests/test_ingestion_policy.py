"""Unit tests for the OpenJM ingestion policy seam (Phase C).

These tests are deterministic and never require the LLM. They verify the
policy seam contract from the Phase C plan:

- TXT returns the fallback policy
- unknown extensions return the safe fallback
- Markdown returns the approved (promoted) policy
- DOCX retains OpenJMDocxKnowledge
- PDF returns the approved policy
- PPTX returns the approved/fallback policy
- policy never exposes unsupported strategy names
- policy decisions are deterministic
- the proven CHUNK_BY_SIZE fallback remains available
"""

from pathlib import Path

import pytest

from app.services.ingestion_policy import (
    FALLBACK_POLICY,
    MARKDOWN_HEADER_POLICY,
    IngestionDecision,
    OpenJMIngestionPolicy,
    ingestion_policy,
)


def _policy():
    return OpenJMIngestionPolicy()


def test_txt_returns_fallback_policy():
    decision = _policy().for_document(Path("notes.txt"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_unknown_extension_returns_safe_fallback():
    decision = _policy().for_document(Path("archive.xyz"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_no_extension_returns_safe_fallback():
    decision = _policy().for_document(Path("README"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_markdown_returns_approved_policy():
    decision = _policy().for_document(Path("report.md"))
    assert decision.chunk_strategy == "CHUNK_BY_MARKDOWN_HEADER"
    assert decision.policy_name == MARKDOWN_HEADER_POLICY


def test_docx_retains_openjm_extractor():
    decision = _policy().for_document(Path("summary.docx"))
    assert decision.knowledge_class_name == "OpenJMDocxKnowledge"
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"


def test_pdf_returns_approved_policy():
    decision = _policy().for_document(Path("invoice.pdf"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_pptx_returns_approved_policy():
    decision = _policy().for_document(Path("deck.pptx"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_html_returns_fallback_policy():
    decision = _policy().for_document(Path("page.html"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_htm_alias_returns_fallback_policy():
    decision = _policy().for_document(Path("page.htm"))
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_policy_only_uses_installed_strategy_names():
    """Every strategy in the table must be a real installed DB-GPT enum."""
    from dbgpt.rag.knowledge.base import ChunkStrategy

    installed = {s.name for s in ChunkStrategy}
    policy = _policy()
    for ext in [".txt", ".md", ".docx", ".pdf", ".pptx", ".html", ".htm", ".xyz"]:
        decision = policy.for_document(Path(f"file{ext}"))
        assert decision.chunk_strategy in installed, (
            f"{ext} references uninstalled strategy {decision.chunk_strategy}"
        )


def test_policy_strategies_supported_by_installed_knowledge():
    """Each decision's strategy must be supported by the matching Knowledge."""
    from dbgpt_ext.rag.knowledge import KnowledgeFactory

    classes = {
        k.document_type().value: k
        for k in KnowledgeFactory.subclasses()
        if k.document_type() is not None
    }
    policy = _policy()
    # .htm has no dedicated installed Knowledge (factory only maps html)
    for ext, doc_type in [
        (".txt", "txt"),
        (".md", "md"),
        (".docx", "docx"),
        (".pdf", "pdf"),
        (".pptx", "pptx"),
        (".html", "html"),
    ]:
        decision = policy.for_document(Path(f"file{ext}"))
        knowledge_class = classes[doc_type]
        supported = {
            s.name for s in knowledge_class.support_chunk_strategy()
        }
        assert decision.chunk_strategy in supported, (
            f"{ext}: {decision.chunk_strategy} not supported by "
            f"{knowledge_class.__name__}"
        )


def test_decisions_are_deterministic():
    policy = _policy()
    for ext in [".txt", ".md", ".docx", ".pdf", ".pptx", ".html", ".xyz"]:
        first = policy.for_document(Path(f"file{ext}"))
        second = policy.for_document(Path(f"file{ext}"))
        assert first == second


def test_fallback_decision_available():
    decision = _policy().fallback_decision()
    assert decision.chunk_strategy == "CHUNK_BY_SIZE"
    assert decision.policy_name == FALLBACK_POLICY


def test_chunk_parameters_kwargs_baseline_defaults():
    """The fallback must leave chunk size/overlap to DB-GPT defaults."""
    decision = _policy().for_document(Path("file.txt"))
    kwargs = decision.chunk_parameters_kwargs()
    assert kwargs == {"chunk_strategy": "CHUNK_BY_SIZE"}


def test_decision_is_immutable_value_object():
    decision = _policy().for_document(Path("file.txt"))
    with pytest.raises(Exception):
        decision.chunk_strategy = "CHUNK_BY_PAGE"


def test_shared_policy_singleton_shape():
    assert isinstance(ingestion_policy(), OpenJMIngestionPolicy)
    assert ingestion_policy().for_document(Path("a.md")).policy_name == (
        MARKDOWN_HEADER_POLICY
    )


def test_decision_exposes_rationale():
    decision = _policy().for_document(Path("file.md"))
    assert isinstance(decision.rationale, str) and decision.rationale


def test_ingestion_uses_policy(monkeypatch):
    """Production ingest must consult the policy (no hard-coded strategy)."""
    import asyncio

    from app.services import knowledge as knowledge_module
    from app.services.knowledge import DBGPTKnowledgeEngine

    captured = {}

    class FakeAssembler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @classmethod
        def load_from_knowledge(cls, **kwargs):
            return cls(**kwargs)

        def persist(self):
            return ["id"]

    class FakePolicy:
        def for_document(self, path):
            return _policy().for_document(path)

    engine = DBGPTKnowledgeEngine.__new__(DBGPTKnowledgeEngine)
    engine.settings = knowledge_module.get_settings()

    monkeypatch.setattr(
        knowledge_module,
        "EmbeddingAssembler",
        FakeAssembler,
    )
    monkeypatch.setattr(
        knowledge_module,
        "KnowledgeFactory",
        type(
            "F",
            (),
            {"from_file_path": staticmethod(lambda p: object())},
        ),
    )
    monkeypatch.setattr(
        knowledge_module,
        "ingestion_policy",
        lambda: FakePolicy(),
    )

    async def fake_to_thread(func, *a, **kw):
        return func()

    monkeypatch.setattr(
        knowledge_module.asyncio, "to_thread", fake_to_thread
    )

    # must reference the patched module-level name
    async def run():
        await engine.ingest_with_strategy(
            "doc-id", Path("sample.md"), strategy_override=None
        )

    monkeypatch.setattr(engine, "_store", lambda doc_id: None)
    asyncio.run(run())

    params = captured["chunk_parameters"]
    assert params.chunk_strategy == "CHUNK_BY_MARKDOWN_HEADER"


def test_ingestion_disabled_raises():
    import asyncio

    from app.services.knowledge import DBGPTKnowledgeEngine, KnowledgeEngineError

    engine = DBGPTKnowledgeEngine.__new__(DBGPTKnowledgeEngine)

    class DisabledSettings:
        knowledge_enabled = False

    engine.settings = DisabledSettings()
    with pytest.raises(KnowledgeEngineError):
        asyncio.run(engine.ingest("doc", Path("x.txt")))
