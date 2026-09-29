from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.schemas import Evidence, ExecutionClass
from app.services.knowledge import knowledge_engine


CATALOG_PATTERNS = (
    "what documents do you have",
    "what documents are loaded",
    "what documents are available",
    "which documents do you have",
    "which documents are loaded",
    "which documents are available",
    "list documents",
    "list the documents",
    "show documents",
    "show me the documents",
    "what files do you have",
    "what files are loaded",
    "what files are available",
)


@dataclass
class ExecutionPlan:
    execution_class: ExecutionClass
    system_prompt: str
    evidence: list[Evidence] = field(default_factory=list)
    direct_answer: str | None = None


class OpenJMOrchestrator:
    """Builds an evidence plan before the model is called."""

    async def _ready_documents(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> list[Document]:
        result = await db.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.status == "ready",
                Document.indexed.is_(True),
            )
            .order_by(Document.created_at.desc())
        )
        return list(result.scalars().all())

    async def plan(
        self,
        message: str,
        db: AsyncSession,
        user_id: str,
    ) -> ExecutionPlan:
        lowered = message.lower().strip()
        documents = await self._ready_documents(db, user_id)

        if any(pattern in lowered for pattern in CATALOG_PATTERNS):
            evidence = [
                Evidence(
                    source_type="document_catalog",
                    source_id=doc.id,
                    title=doc.original_name,
                    passage=(
                        f"{doc.original_name} is indexed and available to this user."
                    ),
                    score=1.0,
                )
                for doc in documents
            ]
            if documents:
                names = "\n".join(f"- {doc.original_name}" for doc in documents)
                answer = f"I currently have access to these indexed documents:\n{names}"
            else:
                answer = "There are currently no indexed documents available to you."
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt=self._knowledge_system_prompt(evidence),
                evidence=evidence,
                direct_answer=answer,
            )

        document_refs = [(doc.id, doc.original_name) for doc in documents]
        evidence = await knowledge_engine.retrieve(message, document_refs)

        if evidence:
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt=self._knowledge_system_prompt(evidence),
                evidence=evidence,
            )

        return ExecutionPlan(
            execution_class="general",
            system_prompt=(
                "You are OpenJM Enterprise AI, a governed enterprise assistant. "
                "Use the conversation history provided by the server. "
                "Do not claim you lack conversation memory when prior turns are present. "
                "Be concise and useful."
            ),
        )

    @staticmethod
    def _knowledge_system_prompt(evidence: list[Evidence]) -> str:
        blocks = []
        for index, item in enumerate(evidence, start=1):
            blocks.append(
                f"[{index}] {item.title}\n{item.passage}"
            )
        context = "\n\n".join(blocks) if blocks else "(no authorized evidence)"
        return (
            "You are OpenJM Enterprise AI. Answer using the authorized evidence below. "
            "Do not claim that you cannot access documents when evidence is present. "
            "If the evidence does not support an answer, say that the available evidence "
            "does not answer the question. Cite sources inline as [1], [2], etc.\n\n"
            f"AUTHORIZED EVIDENCE\n{context}"
        )


orchestrator = OpenJMOrchestrator()
