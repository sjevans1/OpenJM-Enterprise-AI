import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db import get_db
from app.models import Conversation, Message
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationOut,
    Evidence,
    MessageOut,
)
from app.services.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
)
from app.services.orchestrator import orchestrator


router = APIRouter(tags=["chat"])
settings = get_settings()
model_gateway = OpenAICompatibleModelGateway()


def _decode_evidence(raw: str | None) -> list[Evidence]:
    if not raw:
        return []
    try:
        return [Evidence.model_validate(item) for item in json.loads(raw)]
    except (ValueError, TypeError):
        return []


def _message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        execution_class=message.execution_class,
        evidence=_decode_evidence(message.evidence_json),
        created_at=message.created_at,
    )


async def _owned_conversation(
    db: AsyncSession,
    conversation_id: str,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == settings.dev_user_id,
        )
    )
    return result.scalars().first()


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == settings.dev_user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        ConversationOut(
            id=item.id,
            title=item.title,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in result.scalars().all()
    ]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    conversation = await _owned_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_message_out(message) for message in conversation.messages],
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    if request.conversation_id:
        conversation = await _owned_conversation(db, request.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = [
            {"role": item.role, "content": item.content}
            for item in conversation.messages
            if item.role in {"user", "assistant"}
        ]
    else:
        conversation = Conversation(user_id=settings.dev_user_id)
        db.add(conversation)
        await db.flush()
        history = []

    plan = await orchestrator.plan(
        message=request.message,
        db=db,
        user_id=settings.dev_user_id,
    )

    if conversation.title == "New conversation":
        conversation.title = request.message.strip()[:80] or "New conversation"

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=request.message,
    )
    db.add(user_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()

    if plan.direct_answer is not None:
        answer = plan.direct_answer
    else:
        provider_messages = [
            {"role": "system", "content": plan.system_prompt},
            *history[-20:],
            {"role": "user", "content": request.message},
        ]
        try:
            answer = await model_gateway.chat(provider_messages)
        except ModelGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        execution_class=plan.execution_class,
        evidence_json=json.dumps(
            [item.model_dump(mode="json") for item in plan.evidence]
        ),
    )
    db.add(assistant_message)
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(assistant_message)

    return ChatResponse(
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        answer=answer,
        execution_class=plan.execution_class,
        evidence=plan.evidence,
    )
