import asyncio
from functools import cached_property
from pathlib import Path

from dbgpt.core import Chunk
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
from app.services.context_expansion import build_neighbor_requests
from app.services.evidence_quality import (
    RetrievedCandidate,
    deduplicate_exact_candidates,
)


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

    async def ingest(
        self, document_id: str, file_path: Path, source_name: str | None = None
    ) -> None:
        await self.ingest_with_strategy(
            document_id, file_path, strategy_override=None, source_name=source_name
        )

    async def ingest_with_strategy(
        self,
        document_id: str,
        file_path: Path,
        strategy_override: str | None = None,
        extra_params: dict | None = None,
        source_name: str | None = None,
    ) -> None:
        """Ingest one document under the OpenJM ingestion policy.

        ``strategy_override`` and ``extra_params`` exist for the isolated
        comparison harness only; the production ``ingest`` path always
        lets the policy decide.

        ``source_name`` is the customer-visible original filename from the
        upload record. When omitted (direct benchmark/test calls), it
        defaults to ``file_path.name`` so existing callers stay backward
        compatible.
        """
        if not self.settings.knowledge_enabled:
            raise KnowledgeEngineError("Knowledge engine is disabled")

        if source_name is None:
            source_name = file_path.name

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
            enrich_chunks(
                chunks,
                document_id=document_id,
                source_name=source_name,
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
        *,
        neighbor_primary_limit: int = 0,
        neighbor_max_chunks: int = 0,
    ) -> list[Evidence]:
        if not self.settings.knowledge_enabled or not documents:
            return []

        candidates: list[RetrievedCandidate] = []
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
                chunk_id = str(getattr(chunk, "chunk_id", "") or "")
                stored_document_id = str(metadata.get("document_id") or "")
                stored_chunk_id = str(metadata.get("chunk_id") or "")
                if (
                    stored_document_id != document_id
                    or not chunk_id
                    or stored_chunk_id != chunk_id
                ):
                    # Phase D server-owned identities are mandatory on the
                    # hardened read path. Never relabel legacy, contaminated,
                    # or malformed vector rows as authorized evidence.
                    continue
                candidates.append(
                    RetrievedCandidate(
                        document_id=document_id,
                        title=title,
                        chunk_id=chunk_id,
                        content=str(getattr(chunk, "content", "")),
                        score=float(score) if score is not None else None,
                        metadata=metadata,
                    )
                )

        # Phase E E0: exact duplicate passages are grouped BEFORE the final
        # global top-k cut. This prevents repeated copies of the same passage
        # from crowding unique evidence out of the synthesis prompt while
        # preserving every authorized source identity in provenance.
        groups = deduplicate_exact_candidates(candidates)
        selected = groups[: self.settings.rag_top_k]

        primary_evidence: list[Evidence] = []
        for group in selected:
            candidate = group.representative
            # Phase D security: customer-facing Evidence must never leak
            # internal filesystem paths or storage-prefixed names.
            metadata = sanitize_for_evidence(
                dict(candidate.metadata),
                candidate.title,
                candidate.document_id,
            )
            equivalent_sources = [
                {
                    "source_id": item.document_id,
                    "title": item.title,
                    "chunk_id": item.chunk_id,
                }
                for item in group.equivalent_sources
            ]
            provenance = {
                "retrieval_role": "primary",
                "deduplication": "exact_content",
                "duplicate_count": group.duplicate_count,
            }
            if equivalent_sources:
                provenance["equivalent_sources"] = equivalent_sources

            primary_evidence.append(
                Evidence(
                    source_type="document",
                    source_id=candidate.document_id,
                    title=candidate.title,
                    passage=candidate.content[:2000],
                    score=candidate.score,
                    provenance=provenance,
                    metadata=metadata,
                )
            )

        if neighbor_primary_limit <= 0 or neighbor_max_chunks <= 0:
            return primary_evidence

        neighbor_evidence = await self._expand_neighbor_evidence(
            selected,
            primary_limit=neighbor_primary_limit,
            max_neighbor_chunks=neighbor_max_chunks,
        )
        if not neighbor_evidence:
            return primary_evidence

        # Present local context around each primary in natural source order
        # (previous -> primary -> next) while keeping semantic primaries as
        # the governing evidence set and preserving their scores.
        by_origin: dict[tuple[str, str], dict[str, Evidence]] = {}
        for item in neighbor_evidence:
            origin = str(item.provenance.get("expanded_from_chunk_id") or "")
            direction = str(item.provenance.get("direction") or "")
            by_origin.setdefault((item.source_id, origin), {})[direction] = item

        ordered: list[Evidence] = []
        seen_neighbors: set[tuple[str, str]] = set()
        for primary in primary_evidence:
            chunk_id = str(primary.metadata.get("chunk_id") or "")
            nearby = by_origin.get((primary.source_id, chunk_id), {})
            previous = nearby.get("previous")
            if previous is not None:
                identity = (
                    previous.source_id,
                    str(previous.metadata.get("chunk_id") or ""),
                )
                if identity not in seen_neighbors:
                    ordered.append(previous)
                    seen_neighbors.add(identity)

            ordered.append(primary)

            next_item = nearby.get("next")
            if next_item is not None:
                identity = (
                    next_item.source_id,
                    str(next_item.metadata.get("chunk_id") or ""),
                )
                if identity not in seen_neighbors:
                    ordered.append(next_item)
                    seen_neighbors.add(identity)

        # A shared neighbour may have been attached to an origin that was
        # deduplicated from ordering above; append any still-unseen context.
        for item in neighbor_evidence:
            identity = (
                item.source_id,
                str(item.metadata.get("chunk_id") or ""),
            )
            if identity not in seen_neighbors:
                ordered.append(item)
                seen_neighbors.add(identity)

        return ordered

    async def _expand_neighbor_evidence(
        self,
        groups,
        *,
        primary_limit: int,
        max_neighbor_chunks: int,
    ) -> list[Evidence]:
        """Resolve trustworthy immediate neighbours by exact chunk identity.

        Adjacency must be proven in both directions: the semantic primary names
        the neighbour, the stored neighbour points back to the primary, and the
        integer chunk positions differ by exactly one. Missing or inconsistent
        metadata is omitted rather than guessed.
        """

        requests = build_neighbor_requests(
            groups,
            primary_limit=primary_limit,
            max_neighbor_chunks=max_neighbor_chunks,
        )
        if not requests:
            return []

        primary_by_id = {
            (group.representative.document_id, group.representative.chunk_id): (
                group.representative
            )
            for group in groups
        }
        requests_by_document: dict[str, list] = {}
        for request in requests:
            requests_by_document.setdefault(request.document_id, []).append(request)

        resolved: dict[tuple[str, str], Chunk] = {}
        for document_id, document_requests in requests_by_document.items():
            chunks = await self.get_chunks_by_ids(
                document_id,
                [request.chunk_id for request in document_requests],
            )
            for chunk in chunks:
                chunk_id = str(
                    getattr(chunk, "chunk_id", "")
                    or (getattr(chunk, "metadata", {}) or {}).get("chunk_id")
                    or ""
                )
                if chunk_id:
                    resolved[(document_id, chunk_id)] = chunk

        evidence: list[Evidence] = []
        seen: set[tuple[str, str]] = set()
        for request in requests:
            identity = (request.document_id, request.chunk_id)
            if identity in seen:
                continue
            chunk = resolved.get(identity)
            primary = primary_by_id.get(
                (request.document_id, request.expanded_from_chunk_id)
            )
            if chunk is None or primary is None:
                continue

            metadata = dict(getattr(chunk, "metadata", {}) or {})
            if str(metadata.get("document_id") or "") != request.document_id:
                continue
            if str(metadata.get("chunk_id") or "") != request.chunk_id:
                continue

            try:
                primary_index = int(primary.metadata["chunk_index"])
                neighbor_index = int(metadata["chunk_index"])
            except (KeyError, TypeError, ValueError):
                continue

            if request.direction == "previous":
                position_valid = neighbor_index == primary_index - 1
                backlink_valid = (
                    str(metadata.get("next_chunk_id") or "") == primary.chunk_id
                )
            elif request.direction == "next":
                position_valid = neighbor_index == primary_index + 1
                backlink_valid = (
                    str(metadata.get("previous_chunk_id") or "") == primary.chunk_id
                )
            else:
                continue
            if not position_valid or not backlink_valid:
                continue

            safe_metadata = sanitize_for_evidence(
                metadata,
                request.title,
                request.document_id,
            )
            evidence.append(
                Evidence(
                    source_type="document",
                    source_id=request.document_id,
                    title=request.title,
                    passage=str(getattr(chunk, "content", ""))[:2000],
                    score=None,
                    provenance={
                        "retrieval_role": "neighbor",
                        "lookup_mode": "exact_chunk_id",
                        "expanded_from_chunk_id": request.expanded_from_chunk_id,
                        "direction": request.direction,
                    },
                    metadata=safe_metadata,
                )
            )
            seen.add(identity)

        return evidence

    def _get_chunks_by_ids_sync(
        self,
        document_id: str,
        chunk_ids: list[str],
    ) -> list[Chunk]:
        """Read exact chunks from one authorized document collection by id.

        Phase E compatibility seam for DB-GPT 0.8.2: ChromaStore exposes no
        public read-by-id method, while its resolved collection supports
        Collection.get(ids=[...]). Keep that version-specific access contained
        inside the Knowledge adapter.

        Requested order is restored explicitly; Chroma return order is never
        treated as an application invariant.
        """

        requested: list[str] = []
        seen: set[str] = set()
        for chunk_id in chunk_ids:
            value = str(chunk_id or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            requested.append(value)

        if not requested:
            return []

        collection_name = self._collection_name(document_id)
        if not self._chroma_collection_exists(collection_name):
            return []

        store = self._store(document_id)
        collection = getattr(store, "_collection", None)
        if collection is None:
            raise KnowledgeEngineError(
                "DB-GPT Chroma store does not expose its resolved collection"
            )

        result = collection.get(
            ids=requested,
            include=["documents", "metadatas"],
        )
        ids = list(result.get("ids") or [])
        documents = list(result.get("documents") or [])
        metadatas = list(result.get("metadatas") or [])

        by_id: dict[str, Chunk] = {}
        for index, raw_id in enumerate(ids):
            chunk_id = str(raw_id)
            metadata = (
                dict(metadatas[index] or {})
                if index < len(metadatas)
                else {}
            )
            stored_document_id = metadata.get("document_id")
            stored_chunk_id = metadata.get("chunk_id")
            if (
                str(stored_document_id or "") != document_id
                or str(stored_chunk_id or "") != chunk_id
            ):
                # Defense in depth: Phase D chunks carry server-owned
                # document_id and chunk_id. Missing or mismatched identity
                # means the row is not eligible for context expansion.
                continue

            content = (
                str(documents[index] or "")
                if index < len(documents)
                else ""
            )
            by_id[chunk_id] = Chunk(
                content=content,
                metadata=metadata,
                chunk_id=chunk_id,
            )

        return [by_id[item] for item in requested if item in by_id]

    async def get_chunks_by_ids(
        self,
        document_id: str,
        chunk_ids: list[str],
    ) -> list[Chunk]:
        """Async exact-id wrapper used by bounded Phase E context expansion."""

        return await asyncio.to_thread(
            self._get_chunks_by_ids_sync,
            document_id,
            chunk_ids,
        )

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
