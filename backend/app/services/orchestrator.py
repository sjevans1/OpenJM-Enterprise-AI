from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document
from app.schemas import Evidence, ExecutionClass
from app.services.knowledge import knowledge_engine
from app.services.structured_planner import (
    StructuredPlannerError,
    structured_planner,
)
from app.services.tools import (
    ToolContext,
    ToolError,
    tool_registry,
)


settings = get_settings()


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

KNOWLEDGE_CUES = (
    "document",
    "report",
    "policy",
    "file",
    "uploaded",
    "knowledge base",
    "according to",
    "what does the",
    "what did the",
)


@dataclass
class ExecutionPlan:
    execution_class: ExecutionClass
    system_prompt: str
    evidence: list[Evidence] = field(default_factory=list)
    direct_answer: str | None = None


class OpenJMOrchestrator:
    """Builds one governed evidence plan before answer synthesis."""

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

    async def _structured_plan(
        self,
        message: str,
        db: AsyncSession,
        user_id: str,
        conversation_id: str | None,
    ) -> ExecutionPlan | None:
        try:
            decision = await structured_planner.plan(message, db, user_id)
        except StructuredPlannerError:
            return ExecutionPlan(
                execution_class="structured",
                system_prompt="",
                direct_answer=(
                    "I identified this as a structured-data question, but I could not "
                    "produce a safe query plan from the authorized schema. No database "
                    "query was executed."
                ),
            )

        if not decision.candidate:
            return None
        if decision.plan is None:
            return ExecutionPlan(
                execution_class="structured",
                system_prompt="",
                direct_answer=(
                    "This appears to require structured enterprise data, but the "
                    "authorized schema does not support a safe answer to this question. "
                    "No database query was executed and no database value was fabricated."
                ),
            )

        proposal = decision.plan
        context = ToolContext(
            user_id=user_id,
            permissions=frozenset({"structured.read"}),
            conversation_id=conversation_id,
            route="structured",
            model_name=settings.model_name,
            db=db,
        )
        try:
            result = await tool_registry.execute(
                "structured.query",
                context,
                {
                    "source_id": proposal.source_id,
                    "sql": proposal.sql,
                },
            )
        except ToolError:
            return ExecutionPlan(
                execution_class="structured",
                system_prompt="",
                direct_answer=(
                    "I could not safely execute a read-only query for this request. "
                    "No unsupported or write operation was executed."
                ),
            )
        except Exception:
            return ExecutionPlan(
                execution_class="structured",
                system_prompt="",
                direct_answer=(
                    "The authorized data source could not complete this structured "
                    "request safely. No answer was fabricated from unavailable data."
                ),
            )

        return ExecutionPlan(
            execution_class="structured",
            system_prompt=self._structured_system_prompt(result.evidence),
            evidence=result.evidence,
        )

    async def plan(
        self,
        message: str,
        db: AsyncSession,
        user_id: str,
        conversation_id: str | None = None,
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

        # Explicit document/report language retains the existing Knowledge path.
        # This avoids forcing document questions through SQL while Hybrid is deferred.
        knowledge_preferred = bool(documents) and any(
            cue in lowered for cue in KNOWLEDGE_CUES
        )

        if not knowledge_preferred:
            structured = await self._structured_plan(
                message,
                db,
                user_id,
                conversation_id,
            )
            if structured is not None:
                return structured

        document_refs = [(doc.id, doc.original_name) for doc in documents]
        evidence = await knowledge_engine.retrieve(message, document_refs)

        if evidence:
            return ExecutionPlan(
                execution_class="knowledge",
                system_prompt=self._knowledge_system_prompt(evidence),
                evidence=evidence,
            )

        # If explicit Knowledge language produced no evidence, a Structured source
        # may still be able to answer the question without fabricating document facts.
        if knowledge_preferred:
            structured = await self._structured_plan(
                message,
                db,
                user_id,
                conversation_id,
            )
            if structured is not None:
                return structured

        return ExecutionPlan(
            execution_class="general",
            system_prompt=(
                "You are OpenJM Enterprise AI, a governed enterprise assistant. "
                "Use the conversation history provided by the server. "
                "Do not claim you lack conversation memory when prior turns are present. "
                "Do not fabricate enterprise data that was not supplied as evidence. "
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

    @staticmethod
    def _structured_system_prompt(evidence: list[Evidence]) -> str:
        blocks = []
        for index, item in enumerate(evidence, start=1):
            sql = item.metadata.get("sql") or item.provenance.get("executed_sql")
            blocks.append(
                f"[{index}] SOURCE: {item.title}\n"
                f"RESULT: {item.passage}\n"
                f"EXECUTED SQL: {sql or '(not available)'}"
            )
        context = "\n\n".join(blocks) if blocks else "(no authorized evidence)"
        return (
            "You are OpenJM Enterprise AI. Answer only from the authorized structured "
            "evidence below. Treat returned query results as the source of truth. "
            "Do not invent database values, rows, tables or calculations that are not "
            "supported by the evidence. If the result is empty or insufficient, say so. "
            "Cite the structured source inline as [1], [2], etc. The SQL shown is "
            "provenance, not an instruction to execute anything.\n\n"
            f"AUTHORIZED STRUCTURED EVIDENCE\n{context}"
        )


orchestrator = OpenJMOrchestrator()
