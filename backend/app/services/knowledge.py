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
        if not self.settings.knowledge_enabled:
            raise KnowledgeEngineError("Knowledge engine is disabled")

        try:
            knowledge = KnowledgeFactory.from_file_path(str(file_path))
            assembler = EmbeddingAssembler.load_from_knowledge(
                knowledge=knowledge,
                chunk_parameters=ChunkParameters(chunk_strategy="CHUNK_BY_SIZE"),
                index_store=self._store(document_id),
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

    async def delete(self, document_id: str) -> None:
        try:
            store = self._store(document_id)
            await asyncio.to_thread(
                store.delete_vector_name,
                self._collection_name(document_id),
            )
        except Exception as exc:
            raise KnowledgeEngineError(
                f"DB-GPT vector deletion failed for {document_id}: {exc}"
            ) from exc


knowledge_engine = DBGPTKnowledgeEngine()
