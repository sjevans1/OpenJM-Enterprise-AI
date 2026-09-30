import asyncio
from functools import cached_property
from pathlib import Path

from dbgpt.rag.embedding import HuggingFaceEmbeddings
from dbgpt.rag.retriever import EmbeddingRetriever
from dbgpt_ext.rag import ChunkParameters
from dbgpt_ext.rag.assembler import EmbeddingAssembler
from dbgpt_ext.rag.knowledge import KnowledgeFactory
from dbgpt_ext.storage.vector_store.chroma_store import ChromaStore, ChromaVectorConfig

from app.core.config import get_settings
from app.schemas import Evidence
from app.services.chunk_metadata import enrich_chunks, sanitize_for_evidence
from app.services.ingestion_policy import ingestion_policy


_knowledge_extractor_map = {
    ".docx": "app.services.docx_extractor.OpenJMDocxKnowledge",
    ".pptx": "app.services.pptx_extractor.OpenJMPPTXKnowledge",
    ".htm": "app.services.html_extractor.OpenJMHtmlKnowledge",
    ".html": "app.services.html_extractor.OpenJMHtmlKnowledge",
}


def _knowledge_for(file_path: Path):
    """Resolve the OpenJM-owned Knowledge extractor for one file path.

    OpenJM owns two override seams that bypass DB-GPT's extension-driven
    factory:
    * ``.docx`` -> OpenJMDocxKnowledge (Phase B, non-negotiable);
    * ``.htm``   -> OpenJMHtmlKnowledge (Phase D `.htm` compatibility fix,
      because DB-GPT only registers `.html`);
    * ``.pptx``  -> OpenJMPPTXKnowledge (Phase D slide-number metadata);
    * ``.html``  -> kept on the upstream HTMLKnowledge (now via the same
      alias class so policy/strategy validation stays aligned).
    Any other type goes through DB-GPT's ``KnowledgeFactory``.
    """

    suffix = file_path.suffix.lower()
    dotted = _knowledge_extractor_map.get(suffix)
    if dotted is None:
        return KnowledgeFactory.from_file_path(str(file_path))
    module_name, _, class_name = dotted.rpartition(".")
    module = __import__(module_name, fromlist=[class_name])
    cls = getattr(module, class_name)
    return cls(file_path=str(file_path))



class KnowledgeEngineError(RuntimeError):
    pass


class DBGPTKnowledgeEngine:
    """OpenJM-owned adapter around DB-GPT RAG components."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @cached_property
    def embedding_fn(self):
        return HuggingFaceEmbeddings(
            model_name=self.settings.embedding_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    def _collection_name(self, document_id: str) -> str:
        safe = document_id.replace("-", "_")
        return f"{self.settings.vector_collection}_{safe}"

    def _store(self, document_id: str) -> ChromaStore:
        config = ChromaVectorConfig(persist_path=str(self.settings.vector_path))
        return ChromaStore(
            config,
            name=self._collection_name(document_id),
            embedding_fn=self.embedding_fn,
        )

    async def ingest(self, document_id: str, file_path: Path) -> None:
        await self.ingest_with_strategy(
            document_id, file_path, strategy_override=None
        )

    async def ingest_with_strategy(
        self,
        document_id: str,
        file_path: Path,
        strategy_override: str | None = None,
        extra_params: dict | None = None,
    ) -> None:
        """Ingest one document under the OpenJM ingestion policy.

        ``strategy_override`` and ``extra_params`` exist for the isolated
        comparison harness only; the production ``ingest`` path always
        lets the policy decide.
        """
        if not self.settings.knowledge_enabled:
            raise KnowledgeEngineError("Knowledge engine is disabled")

        try:
            decision = ingestion_policy().for_document(file_path)
            knowledge = _knowledge_for(file_path)
            parameters = dict(decision.chunk_parameters_kwargs())
            if strategy_override is not None:
                parameters["chunk_strategy"] = strategy_override
            if extra_params:
                parameters.update(extra_params)
            assembler = EmbeddingAssembler.load_from_knowledge(
                knowledge=knowledge,
                chunk_parameters=ChunkParameters(**parameters),
                index_store=self._store(document_id),
            )
            # Phase D structural metadata enrichment. Runs after the splitter
            # has produced chunks (so chunk content and scores are unchanged)
            # and before persist (so enriched metadata lands in Chroma). All
            # enrichment fields are server-owned and derived from server
            # identities or detected document structure; nothing is fabricated.
            chunks = assembler.get_chunks()
            original_name = file_path.name
            enrich_chunks(
                chunks,
                document_id=document_id,
                source_name=original_name,
                policy_name=decision.policy_name,
            )
            await asyncio.to_thread(assembler.persist)
        except Exception as exc:
            raise KnowledgeEngineError(
                f"DB-GPT ingestion failed for {file_path.name}: {exc}"
            ) from exc

    async def retrieve(
        self,
        query: str,
        documents: list[tuple[str, str]],
    ) -> list[Evidence]:
        if not self.settings.knowledge_enabled or not documents:
            return []

        evidence: list[Evidence] = []
        for document_id, title in documents:
            try:
                retriever = EmbeddingRetriever(
                    top_k=self.settings.rag_top_k,
                    index_store=self._store(document_id),
                )
                chunks = await retriever.aretrieve_with_scores(
                    query,
                    score_threshold=self.settings.rag_score_threshold,
                )
            except Exception as exc:
                raise KnowledgeEngineError(
                    f"DB-GPT retrieval failed for {title}: {exc}"
                ) from exc

            for chunk in chunks:
                metadata = dict(getattr(chunk, "metadata", {}) or {})
                score = getattr(chunk, "score", None)
                # Phase D security: customer-facing Evidence must never leak
                # internal filesystem paths. Loaders store ``self._path`` in
                # the ``source`` metadata key (and the path can also appear in
                # ``title`` for PDF). Sanitize before exposing; server-owned
                # structural keys are preserved untouched.
                metadata = sanitize_for_evidence(metadata, title)
                evidence.append(
                    Evidence(
                        source_type="document",
                        source_id=document_id,
                        title=title,
                        passage=str(getattr(chunk, "content", ""))[:2000],
                        score=float(score) if score is not None else None,
                        metadata=metadata,
                    )
                )

        def sort_key(item: Evidence) -> float:
            return item.score if item.score is not None else 0.0

        evidence.sort(key=sort_key, reverse=True)
        return evidence[: self.settings.rag_top_k]

    def _chroma_collection_exists(self, collection_name: str) -> bool:
        """Check Chroma collection existence without creating the collection."""
        from chromadb import PersistentClient, Settings

        persist_dir = self.settings.vector_path / "chromadb"
        client = PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        names = {
            item if isinstance(item, str) else getattr(item, "name", None)
            for item in client.list_collections()
        }
        return collection_name in names

    def _force_delete_chroma_collection(self, collection_name: str) -> None:
        """Delete a Chroma collection directly when DB-GPT leaves it behind.

        DB-GPT 0.8.2's ChromaStore.delete_vector_name() can no-op because its
        collection-existence check compares a string name with the objects
        returned by Chroma's list_collections(). OpenJM verifies the deletion
        and uses Chroma's public PersistentClient API only as a cleanup fallback.
        """
        from chromadb import PersistentClient, Settings

        persist_dir = self.settings.vector_path / "chromadb"
        client = PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            client.delete_collection(collection_name)
        except Exception:
            if self._chroma_collection_exists(collection_name):
                raise

    async def delete(self, document_id: str) -> None:
        collection_name = self._collection_name(document_id)
        try:
            store = self._store(document_id)
            await asyncio.to_thread(store.delete_vector_name, collection_name)

            # Verify the collection itself is gone, not merely empty. DB-GPT's
            # vector_name_exists() checks count()>0 and therefore cannot detect
            # an empty orphan collection.
            if await asyncio.to_thread(
                self._chroma_collection_exists,
                collection_name,
            ):
                await asyncio.to_thread(
                    self._force_delete_chroma_collection,
                    collection_name,
                )

            if await asyncio.to_thread(
                self._chroma_collection_exists,
                collection_name,
            ):
                raise RuntimeError(
                    f"Chroma collection {collection_name} still exists after delete"
                )
        except Exception as exc:
            raise KnowledgeEngineError(
                f"DB-GPT vector deletion failed for {document_id}: {exc}"
            ) from exc


knowledge_engine = DBGPTKnowledgeEngine()
