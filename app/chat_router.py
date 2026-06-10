import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat_schemas import (
    ChatAnalyticsResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatMessageSchema,
    ChatWSOutgoing,
    ConversationDetail,
    ConversationSummary,
    FAQCreate,
    FAQResponse,
    FAQUpdate,
    SatisfactionRequest,
    WSMessageType,
)
from app.chat_service import get_chat_service
from app.database import async_session_factory, get_db
from app.dependencies import get_admin_user, get_current_user_optional
from app.exceptions import NotFoundError
from app.faq_models import FAQ, SupportTicket
from app.config import get_settings
from app.llm_service import LLMService
from app.models import User
from app.vector_search import VectorSearchService
from app.websocket_manager import manager, resolve_session_id, safe_receive_json

router = APIRouter(tags=["chat"])
chatbot_router = APIRouter(prefix="/chatbot", tags=["chatbot"])


@router.websocket("/chat/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    session_id = websocket.query_params.get("session_id") or str(uuid.uuid4())
    token = websocket.query_params.get("token")
    await manager.connect(websocket, session_id)

    try:
        while True:
            incoming = await safe_receive_json(websocket)
            if incoming is None:
                break

            if incoming.type == "ping":
                await manager.handle_ping(websocket)
                continue

            if incoming.type == "satisfaction" and incoming.score is not None:
                active_session = incoming.session_id or session_id
                async with async_session_factory() as db:
                    service = get_chat_service()
                    await service.record_satisfaction(db, active_session, incoming.score)
                await manager.send_model(
                    websocket,
                    ChatWSOutgoing(
                        type=WSMessageType.COMPLETE,
                        session_id=active_session,
                        message="Satisfaction recorded",
                    ),
                )
                continue

            if not incoming.content:
                await manager.send_model(
                    websocket,
                    ChatWSOutgoing(type=WSMessageType.ERROR, message="Message content required"),
                )
                continue

            active_session = resolve_session_id(incoming) if incoming.session_id else session_id
            await manager.broadcast_typing(active_session, True)

            async with async_session_factory() as db:
                user: User | None = None
                if token:
                    from app.dependencies import resolve_user_from_token_optional

                    user = await resolve_user_from_token_optional(token, db)

                service = get_chat_service()
                result = await service.process_message(
                    db,
                    session_id=active_session,
                    user_message=incoming.content,
                    user=user,
                    product_id=incoming.product_id,
                    stream=True,
                )

                full_response = ""
                stream = result["stream"]
                async for chunk in stream:
                    full_response += chunk
                    await manager.send_model(
                        websocket,
                        ChatWSOutgoing(
                            type=WSMessageType.CHUNK,
                            content=chunk,
                            session_id=active_session,
                        ),
                    )

                await manager.broadcast_typing(active_session, False)
                stream_meta = result["stream_meta"]
                complete = ChatWSOutgoing(
                    type=WSMessageType.COMPLETE,
                    content=full_response,
                    session_id=active_session,
                    conversation_id=result["conversation_id"],
                    recommendations=result["recommendations"],
                    confidence=stream_meta.get("confidence"),
                    ticket_id=stream_meta.get("ticket_id"),
                )
                await manager.send_model(websocket, complete)
                if stream_meta.get("ticket_id"):
                    await manager.send_model(
                        websocket,
                        ChatWSOutgoing(
                            type=WSMessageType.ESCALATION,
                            session_id=active_session,
                            ticket_id=stream_meta["ticket_id"],
                            message="Your request has been escalated to a human agent.",
                        ),
                    )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)


@router.post("/chat/message", response_model=ChatMessageResponse)
async def chat_message(
    payload: ChatMessageRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)] = None,
) -> ChatMessageResponse:
    session_id = payload.session_id or str(uuid.uuid4())
    service = get_chat_service()
    result = await service.process_message(
        db,
        session_id=session_id,
        user_message=payload.content,
        user=current_user,
        product_id=payload.product_id,
        stream=False,
    )
    return ChatMessageResponse(
        answer=result["answer"],
        confidence=result["confidence"],
        conversation_id=result["conversation_id"],
        session_id=session_id,
        products=result["products"],
        recommendations=result["recommendations"],
        ticket_id=result["ticket_id"],
    )


@chatbot_router.get("/faq", response_model=list[FAQResponse])
async def list_faqs(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> list[FAQ]:
    return list((await db.execute(select(FAQ).order_by(FAQ.category, FAQ.id))).scalars().all())


@chatbot_router.post("/faq", response_model=FAQResponse, status_code=201)
async def create_faq(
    payload: FAQCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> FAQ:
    faq = FAQ(**payload.model_dump())
    db.add(faq)
    await db.commit()
    await db.refresh(faq)
    if get_settings().openai_api_key:
        vector = VectorSearchService(LLMService())
        await vector.upsert_faq_embedding(db, faq)
        await db.refresh(faq)
    return faq


@chatbot_router.put("/faq/{faq_id}", response_model=FAQResponse)
async def update_faq(
    faq_id: int,
    payload: FAQUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> FAQ:
    result = await db.execute(select(FAQ).where(FAQ.id == faq_id))
    faq = result.scalar_one_or_none()
    if faq is None:
        raise NotFoundError("FAQ not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(faq, field, value)
    await db.commit()
    await db.refresh(faq)
    if get_settings().openai_api_key:
        vector = VectorSearchService(LLMService())
        await vector.upsert_faq_embedding(db, faq)
        await db.refresh(faq)
    return faq


@chatbot_router.delete("/faq/{faq_id}", status_code=204)
async def delete_faq(
    faq_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> None:
    result = await db.execute(select(FAQ).where(FAQ.id == faq_id))
    faq = result.scalar_one_or_none()
    if faq is None:
        raise NotFoundError("FAQ not found")
    await db.execute(delete(FAQ).where(FAQ.id == faq_id))
    await db.commit()


@chatbot_router.get("/conversations")
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    service = get_chat_service()
    conversations, total = await service.list_conversations(db, page=page, page_size=page_size)
    items = [
        ConversationSummary(
            id=conversation.id,
            session_id=conversation.session_id,
            user_id=conversation.user_id,
            satisfaction_score=conversation.satisfaction_score,
            converted_to_order=conversation.converted_to_order,
            message_count=len(conversation.messages),
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation in conversations
    ]
    total_pages = math.ceil(total / page_size) if total else 0
    return {
        "items": items,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1 and total_pages > 0,
        },
    }


@chatbot_router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> ConversationDetail:
    from sqlalchemy.orm import selectinload
    from app.faq_models import ChatConversation

    result = await db.execute(
        select(ChatConversation)
        .options(selectinload(ChatConversation.messages))
        .where(ChatConversation.id == conversation_id),
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found")
    return ConversationDetail(
        id=conversation.id,
        session_id=conversation.session_id,
        user_id=conversation.user_id,
        satisfaction_score=conversation.satisfaction_score,
        converted_to_order=conversation.converted_to_order,
        message_count=len(conversation.messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ChatMessageSchema(
                id=message.id,
                role=message.role.value,
                content=message.content,
                confidence_score=message.confidence_score,
                metadata=message.metadata_,
                created_at=message.created_at,
            )
            for message in conversation.messages
        ],
    )


@chatbot_router.get("/tickets")
async def list_support_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> list[dict[str, object]]:
    tickets = list((await db.execute(select(SupportTicket).order_by(SupportTicket.created_at.desc()))).scalars().all())
    return [
        {
            "id": ticket.id,
            "conversation_id": str(ticket.conversation_id),
            "user_id": str(ticket.user_id) if ticket.user_id else None,
            "subject": ticket.subject,
            "description": ticket.description,
            "status": ticket.status.value,
            "confidence_score": ticket.confidence_score,
            "created_at": ticket.created_at,
        }
        for ticket in tickets
    ]


@chatbot_router.get("/analytics", response_model=ChatAnalyticsResponse)
async def get_chat_analytics(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> ChatAnalyticsResponse:
    service = get_chat_service()
    return await service.get_analytics(db)


@chatbot_router.post("/satisfaction", status_code=204)
async def submit_satisfaction(
    payload: SatisfactionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User | None, Depends(get_current_user_optional)] = None,
) -> None:
    service = get_chat_service()
    await service.record_satisfaction(db, payload.session_id, payload.score)
