import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.faq_models import AnalyticsEventType, ChatAnalyticsEvent, ChatConversation


async def mark_chat_conversion(db: AsyncSession, user_id: uuid.UUID) -> None:
    result = await db.execute(
        select(ChatConversation).where(
            ChatConversation.user_id == user_id,
            ChatConversation.converted_to_order.is_(False),
        ),
    )
    conversations = result.scalars().all()
    for conversation in conversations:
        conversation.converted_to_order = True
        db.add(
            ChatAnalyticsEvent(
                event_type=AnalyticsEventType.CONVERSION,
                conversation_id=conversation.id,
            ),
        )
    await db.commit()
