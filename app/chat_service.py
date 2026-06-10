import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from sqlalchemy import asc, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.chat_schemas import ChatAnalyticsResponse, ProductSnippet, TopQuestionStat, ProductDiscussionStat
from app.config import get_settings
from app.faq_models import (
    AnalyticsEventType,
    ChatAnalyticsEvent,
    ChatConversation,
    ChatMessage,
    ChatRole,
    SupportTicket,
    TicketStatus,
)
from app.llm_service import LLMService
from app.models import Product, User
from app.redis_client import get_redis
from app.vector_search import RetrievedDocument, VectorSearchService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional, helpful, modern e-commerce shopping assistant.
Rules:
- Only use the provided product, FAQ, and policy context. Never invent product details.
- If information is unavailable, clearly say so.
- Be concise, friendly, and sales-aware while remaining truthful.
- When comparing products, reference only provided data.
- Remember prior conversation context including budget and preferences.
- Suggest relevant products when appropriate using provided recommendations."""


class ChatMemoryStore:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis = get_redis()

    def _history_key(self, session_id: str) -> str:
        return f"chat:history:{session_id}"

    def _context_key(self, session_id: str) -> str:
        return f"chat:context:{session_id}"

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        raw = await self._redis.get(self._history_key(session_id))
        if not raw:
            return []
        return json.loads(raw)

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        history = await self.get_history(session_id)
        history.append({"role": role, "content": content})
        max_messages = self._settings.chat_history_max_messages
        history = history[-max_messages:]
        await self._redis.set(
            self._history_key(session_id),
            json.dumps(history),
            ex=self._settings.chat_session_ttl_seconds,
        )

    async def get_context(self, session_id: str) -> dict[str, Any]:
        raw = await self._redis.get(self._context_key(session_id))
        if not raw:
            return {
                "budget": None,
                "interests": [],
                "viewed_products": [],
                "preferences": {},
            }
        return json.loads(raw)

    async def update_context(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        context = await self.get_context(session_id)
        for key, value in updates.items():
            if key == "viewed_products" and isinstance(value, int):
                viewed = context.setdefault("viewed_products", [])
                if value not in viewed:
                    viewed.append(value)
                context["viewed_products"] = viewed[-20:]
            elif key == "interests" and isinstance(value, str):
                interests = context.setdefault("interests", [])
                if value not in interests:
                    interests.append(value)
                context["interests"] = interests[-10:]
            else:
                context[key] = value
        await self._redis.set(
            self._context_key(session_id),
            json.dumps(context),
            ex=self._settings.chat_session_ttl_seconds,
        )
        return context


class ChatService:
    def __init__(self, llm: LLMService, vector_search: VectorSearchService) -> None:
        self._llm = llm
        self._vector = vector_search
        self._memory = ChatMemoryStore()
        self._settings = get_settings()

    def _extract_budget(self, text: str) -> Decimal | None:
        patterns = [
            r"under\s*\$?\s*(\d+(?:\.\d{1,2})?)",
            r"below\s*\$?\s*(\d+(?:\.\d{1,2})?)",
            r"budget\s*(?:of|is)?\s*\$?\s*(\d+(?:\.\d{1,2})?)",
            r"less than\s*\$?\s*(\d+(?:\.\d{1,2})?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return Decimal(match.group(1))
        return None

    def _extract_product_ids(self, text: str) -> list[int]:
        ids = re.findall(r"product\s*#?\s*(\d+)", text, re.IGNORECASE)
        return [int(value) for value in ids]

    def _extract_interests(self, text: str) -> list[str]:
        interests: list[str] = []
        patterns = [
            r"(?:i need|i want|looking for|searching for|interested in|help me find)\s+(.+?)(?:\.|$|,)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value = match.group(1).strip()
                if len(value) > 2:
                    interests.append(value)
        return interests

    def _extract_preferences(self, text: str) -> dict[str, str]:
        preferences: dict[str, str] = {}
        brand_match = re.search(r"(?:prefer|like)\s+([A-Za-z0-9-]+)\s+brand", text, re.IGNORECASE)
        if brand_match:
            preferences["brand"] = brand_match.group(1)
        color_match = re.search(r"(?:prefer|like)\s+([A-Za-z]+)\s+color", text, re.IGNORECASE)
        if color_match:
            preferences["color"] = color_match.group(1)
        size_match = re.search(r"size\s+([A-Za-z0-9]+)", text, re.IGNORECASE)
        if size_match:
            preferences["size"] = size_match.group(1)
        return preferences

    async def _get_or_create_conversation(
        self,
        db: AsyncSession,
        session_id: str,
        user: User | None,
    ) -> ChatConversation:
        result = await db.execute(
            select(ChatConversation).where(ChatConversation.session_id == session_id),
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            conversation = ChatConversation(
                session_id=session_id,
                user_id=user.id if user else None,
            )
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
        elif user and conversation.user_id is None:
            conversation.user_id = user.id
            await db.commit()
            await db.refresh(conversation)
        return conversation

    async def _save_message(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        role: ChatRole,
        content: str,
        *,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            confidence_score=confidence,
            metadata_=metadata,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def _track_event(
        self,
        db: AsyncSession,
        event_type: AnalyticsEventType,
        *,
        conversation_id: uuid.UUID | None = None,
        product_id: int | None = None,
        question_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = ChatAnalyticsEvent(
            event_type=event_type,
            conversation_id=conversation_id,
            product_id=product_id,
            question_text=question_text,
            event_metadata=metadata,
        )
        db.add(event)
        await db.commit()

    def _compute_confidence(
        self,
        retrieved: list[RetrievedDocument],
        answer: str,
    ) -> float:
        if not retrieved:
            return 0.2
        top_score = max(doc.score for doc in retrieved)
        unknown_markers = ("i don't know", "not available", "unable to find", "no information")
        penalty = 0.25 if any(marker in answer.lower() for marker in unknown_markers) else 0.0
        return max(0.0, min(1.0, top_score - penalty))

    async def _build_recommendations(
        self,
        db: AsyncSession,
        *,
        product_id: int | None,
        category_id: int | None,
        budget: Decimal | None,
        interests: list[str],
    ) -> list[ProductSnippet]:
        recommendations: list[ProductSnippet] = []

        if product_id is not None:
            co_purchase = await self._co_purchased_products(db, product_id, limit=3)
            recommendations.extend(co_purchase)
            product = await db.get(Product, product_id)
            if product:
                similar = await self._category_products(
                    db,
                    category_id=product.category_id,
                    exclude_id=product.id,
                    limit=3,
                )
                recommendations.extend(similar)
                recommendations.extend(
                    await self._price_alternatives(db, product, budget=budget),
                )

        if category_id is not None and len(recommendations) < 4:
            recommendations.extend(
                await self._category_products(db, category_id=category_id, limit=4),
            )

        if budget is not None:
            budget_items = await self._budget_products(db, budget, limit=4)
            recommendations.extend(budget_items)

        if interests:
            interest_query = " ".join(interests)
            docs = await self._vector.search_products(db, interest_query, limit=3)
            ids = [int(doc.source_id) for doc in docs if doc.source_type == "product"]
            recommendations.extend(await self._products_to_snippets(db, ids))

        deduped: dict[int, ProductSnippet] = {}
        for item in recommendations:
            deduped[item.id] = item
        return list(deduped.values())[:8]

    async def _co_purchased_products(
        self,
        db: AsyncSession,
        product_id: int,
        *,
        limit: int,
    ) -> list[ProductSnippet]:
        sql = text(
            """
            SELECT oi2.product_id, COUNT(*) AS cnt
            FROM order_items oi1
            JOIN order_items oi2 ON oi1.order_id = oi2.order_id
            WHERE oi1.product_id = :product_id AND oi2.product_id != :product_id
            GROUP BY oi2.product_id
            ORDER BY cnt DESC
            LIMIT :limit
            """,
        )
        rows = (await db.execute(sql, {"product_id": product_id, "limit": limit})).all()
        ids = [row[0] for row in rows]
        return await self._products_to_snippets(db, ids)

    async def _category_products(
        self,
        db: AsyncSession,
        *,
        category_id: int,
        exclude_id: int | None = None,
        limit: int = 4,
    ) -> list[ProductSnippet]:
        stmt = select(Product.id).where(Product.category_id == category_id)
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        stmt = stmt.order_by(desc(Product.created_at)).limit(limit)
        ids = [row[0] for row in (await db.execute(stmt)).all()]
        return await self._products_to_snippets(db, ids)

    async def _price_alternatives(
        self,
        db: AsyncSession,
        product: Product,
        *,
        budget: Decimal | None,
    ) -> list[ProductSnippet]:
        higher = (
            select(Product.id)
            .where(
                Product.category_id == product.category_id,
                Product.id != product.id,
                Product.price > product.price,
            )
            .order_by(asc(Product.price))
            .limit(2)
        )
        lower = (
            select(Product.id)
            .where(
                Product.category_id == product.category_id,
                Product.id != product.id,
                Product.price < product.price,
            )
            .order_by(desc(Product.price))
            .limit(2)
        )
        if budget is not None:
            lower = lower.where(Product.price <= budget)
        higher_ids = [row[0] for row in (await db.execute(higher)).all()]
        lower_ids = [row[0] for row in (await db.execute(lower)).all()]
        return await self._products_to_snippets(db, higher_ids + lower_ids)

    async def _budget_products(
        self,
        db: AsyncSession,
        budget: Decimal,
        *,
        limit: int,
    ) -> list[ProductSnippet]:
        stmt = (
            select(Product.id)
            .where(Product.price <= budget, Product.stock_quantity > 0)
            .order_by(desc(Product.stock_quantity), asc(Product.price))
            .limit(limit)
        )
        ids = [row[0] for row in (await db.execute(stmt)).all()]
        return await self._products_to_snippets(db, ids)

    async def _products_to_snippets(
        self,
        db: AsyncSession,
        product_ids: list[int],
    ) -> list[ProductSnippet]:
        if not product_ids:
            return []
        contexts = await self._vector.load_product_context(db, product_ids)
        return [ProductSnippet.model_validate(item) for item in contexts]

    def _format_context(
        self,
        retrieved: list[RetrievedDocument],
        products: list[dict[str, object]],
        recommendations: list[ProductSnippet],
        memory_context: dict[str, Any],
        order_status: str | None,
    ) -> str:
        sections: list[str] = []
        if memory_context.get("budget"):
            sections.append(f"Customer budget: ${memory_context['budget']}")
        if memory_context.get("interests"):
            sections.append(f"Customer interests: {', '.join(memory_context['interests'])}")
        if memory_context.get("viewed_products"):
            sections.append(f"Viewed product IDs: {memory_context['viewed_products']}")
        if memory_context.get("preferences"):
            sections.append(f"Preferences: {json.dumps(memory_context['preferences'])}")

        if order_status:
            sections.append(f"Order status info: {order_status}")

        if retrieved:
            sections.append("Retrieved knowledge:")
            for doc in retrieved:
                sections.append(
                    f"[{doc.source_type}:{doc.source_id}] {doc.title}\n{doc.content}\n(score={doc.score:.3f})",
                )

        if products:
            sections.append("Referenced products:")
            for product in products:
                sections.append(json.dumps(product, default=str))

        if recommendations:
            sections.append("Recommendations:")
            for item in recommendations:
                sections.append(
                    f"- {item.title} (${item.price}) stock={item.stock_quantity} id={item.id}",
                )
        return "\n\n".join(sections)

    async def _resolve_order_status(
        self,
        db: AsyncSession,
        user: User | None,
        message: str,
    ) -> str | None:
        if user is None:
            return None
        if "order status" not in message.lower() and "my order" not in message.lower():
            return None
        from app.models import Order

        stmt = (
            select(Order)
            .where(Order.user_id == user.id)
            .order_by(desc(Order.created_at))
            .limit(3)
        )
        orders = (await db.execute(stmt)).scalars().all()
        if not orders:
            return "No orders found for this customer."
        lines = []
        for order in orders:
            lines.append(
                f"Order #{order.id}: payment={order.payment_status.value}, "
                f"shipping={order.shipping_status.value}, total=${order.total_amount}",
            )
        return "\n".join(lines)

    async def _maybe_escalate(
        self,
        db: AsyncSession,
        conversation: ChatConversation,
        user: User | None,
        user_message: str,
        assistant_answer: str,
        confidence: float,
    ) -> SupportTicket | None:
        if confidence >= self._settings.chat_confidence_threshold:
            return None
        ticket = SupportTicket(
            conversation_id=conversation.id,
            user_id=user.id if user else None,
            subject="Low-confidence chatbot escalation",
            description=f"User: {user_message}\nAssistant: {assistant_answer}",
            status=TicketStatus.ESCALATED,
            confidence_score=confidence,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        await self._track_event(
            db,
            AnalyticsEventType.ESCALATION,
            conversation_id=conversation.id,
            metadata={"ticket_id": ticket.id, "confidence": confidence},
        )
        logger.warning(
            "Escalated conversation %s to human agent (ticket_id=%s, confidence=%.2f)",
            conversation.id,
            ticket.id,
            confidence,
        )
        return ticket

    async def process_message(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        user_message: str,
        user: User | None = None,
        product_id: int | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        conversation = await self._get_or_create_conversation(db, session_id, user)
        await self._save_message(db, conversation.id, ChatRole.USER, user_message)
        await self._memory.append_history(session_id, "user", user_message)

        budget = self._extract_budget(user_message)
        memory_context = await self._memory.get_context(session_id)
        if budget is not None:
            memory_context = await self._memory.update_context(session_id, {"budget": str(budget)})
        else:
            budget_value = memory_context.get("budget")
            budget = Decimal(budget_value) if budget_value else None

        for interest in self._extract_interests(user_message):
            memory_context = await self._memory.update_context(session_id, {"interests": interest})

        preferences = self._extract_preferences(user_message)
        if preferences:
            merged_preferences = memory_context.get("preferences", {})
            merged_preferences.update(preferences)
            memory_context = await self._memory.update_context(
                session_id,
                {"preferences": merged_preferences},
            )

        explicit_ids = self._extract_product_ids(user_message)
        if product_id is not None:
            explicit_ids.append(product_id)
            await self._memory.update_context(session_id, {"viewed_products": product_id})

        category_id: int | None = None
        if explicit_ids:
            product = await db.get(Product, explicit_ids[0])
            if product:
                category_id = product.category_id

        retrieved = await self._vector.hybrid_search(
            db,
            user_message,
            max_price=budget,
            category_id=category_id,
        )
        product_context = await self._vector.load_product_context(db, list(set(explicit_ids)))
        recommendations = await self._build_recommendations(
            db,
            product_id=explicit_ids[0] if explicit_ids else None,
            category_id=category_id,
            budget=budget,
            interests=memory_context.get("interests", []),
        )
        order_status = await self._resolve_order_status(db, user, user_message)
        context_block = self._format_context(
            retrieved,
            product_context,
            recommendations,
            memory_context,
            order_status,
        )

        history = await self._memory.get_history(session_id)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context_block:
            messages.append({"role": "system", "content": f"Context:\n{context_block}"})
        messages.extend(history)

        if stream:
            stream_meta: dict[str, Any] = {"ticket_id": None, "confidence": None}
            return {
                "stream": self._stream_response(
                    db,
                    session_id=session_id,
                    conversation=conversation,
                    user=user,
                    user_message=user_message,
                    messages=messages,
                    recommendations=recommendations,
                    retrieved=retrieved,
                    product_context=product_context,
                    stream_meta=stream_meta,
                ),
                "conversation_id": conversation.id,
                "recommendations": recommendations,
                "stream_meta": stream_meta,
            }

        answer = await self._llm.chat_completion(messages)
        confidence = self._compute_confidence(retrieved, answer)
        ticket = await self._maybe_escalate(
            db,
            conversation,
            user,
            user_message,
            answer,
            confidence,
        )

        metadata = {
            "products": product_context,
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "retrieved_sources": [
                {"type": doc.source_type, "id": doc.source_id, "score": doc.score}
                for doc in retrieved
            ],
        }
        await self._save_message(
            db,
            conversation.id,
            ChatRole.ASSISTANT,
            answer,
            confidence=confidence,
            metadata=metadata,
        )
        await self._memory.append_history(session_id, "assistant", answer)
        await self._track_event(
            db,
            AnalyticsEventType.QUESTION,
            conversation_id=conversation.id,
            question_text=user_message,
        )
        for product in product_context:
            await self._track_event(
                db,
                AnalyticsEventType.PRODUCT_MENTION,
                conversation_id=conversation.id,
                product_id=int(product["id"]),
            )
        if recommendations:
            await self._track_event(
                db,
                AnalyticsEventType.RECOMMENDATION,
                conversation_id=conversation.id,
                metadata={"count": len(recommendations)},
            )

        return {
            "answer": answer,
            "confidence": confidence,
            "conversation_id": conversation.id,
            "products": [ProductSnippet.model_validate(p) for p in product_context],
            "recommendations": recommendations,
            "ticket_id": ticket.id if ticket else None,
        }

    async def _stream_response(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        conversation: ChatConversation,
        user: User | None,
        user_message: str,
        messages: list[dict[str, str]],
        recommendations: list[ProductSnippet],
        retrieved: list[RetrievedDocument],
        product_context: list[dict[str, object]],
        stream_meta: dict[str, Any],
    ) -> AsyncIterator[str]:
        parts: list[str] = []
        async for chunk in self._llm.chat_completion_stream(messages):
            parts.append(chunk)
            yield chunk

        answer = "".join(parts)
        confidence = self._compute_confidence(retrieved, answer)
        ticket = await self._maybe_escalate(
            db,
            conversation,
            user,
            user_message,
            answer,
            confidence,
        )
        metadata = {
            "products": product_context,
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "ticket_id": ticket.id if ticket else None,
        }
        await self._save_message(
            db,
            conversation.id,
            ChatRole.ASSISTANT,
            answer,
            confidence=confidence,
            metadata=metadata,
        )
        await self._memory.append_history(session_id, "assistant", answer)
        await self._track_event(
            db,
            AnalyticsEventType.QUESTION,
            conversation_id=conversation.id,
            question_text=user_message,
        )
        stream_meta["ticket_id"] = ticket.id if ticket else None
        stream_meta["confidence"] = confidence

    async def record_satisfaction(
        self,
        db: AsyncSession,
        session_id: str,
        score: int,
    ) -> None:
        result = await db.execute(
            select(ChatConversation).where(ChatConversation.session_id == session_id),
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            return
        conversation.satisfaction_score = score
        await db.commit()
        await self._track_event(
            db,
            AnalyticsEventType.SATISFACTION,
            conversation_id=conversation.id,
            metadata={"score": score},
        )

    async def get_analytics(self, db: AsyncSession) -> ChatAnalyticsResponse:
        total_conversations = int(
            (await db.execute(select(func.count()).select_from(ChatConversation))).scalar_one(),
        )
        total_questions = int(
            (
                await db.execute(
                    select(func.count()).select_from(ChatAnalyticsEvent).where(
                        ChatAnalyticsEvent.event_type == AnalyticsEventType.QUESTION,
                    ),
                )
            ).scalar_one(),
        )
        escalation_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(ChatAnalyticsEvent).where(
                        ChatAnalyticsEvent.event_type == AnalyticsEventType.ESCALATION,
                    ),
                )
            ).scalar_one(),
        )
        converted = int(
            (
                await db.execute(
                    select(func.count()).select_from(ChatConversation).where(
                        ChatConversation.converted_to_order.is_(True),
                    ),
                )
            ).scalar_one(),
        )
        avg_satisfaction = (
            await db.execute(
                select(func.avg(ChatConversation.satisfaction_score)).where(
                    ChatConversation.satisfaction_score.is_not(None),
                ),
            )
        ).scalar_one()

        top_questions_rows = (
            await db.execute(
                select(ChatAnalyticsEvent.question_text, func.count())
                .where(
                    ChatAnalyticsEvent.event_type == AnalyticsEventType.QUESTION,
                    ChatAnalyticsEvent.question_text.is_not(None),
                )
                .group_by(ChatAnalyticsEvent.question_text)
                .order_by(desc(func.count()))
                .limit(10),
            )
        ).all()

        product_rows = (
            await db.execute(
                select(
                    ChatAnalyticsEvent.product_id,
                    Product.title,
                    func.count(),
                )
                .join(Product, Product.id == ChatAnalyticsEvent.product_id)
                .where(ChatAnalyticsEvent.event_type == AnalyticsEventType.PRODUCT_MENTION)
                .group_by(ChatAnalyticsEvent.product_id, Product.title)
                .order_by(desc(func.count()))
                .limit(10),
            )
        ).all()

        conversion_rate = (converted / total_conversations) if total_conversations else 0.0
        return ChatAnalyticsResponse(
            total_conversations=total_conversations,
            total_questions=total_questions,
            escalation_count=escalation_count,
            conversion_rate=round(conversion_rate, 4),
            average_satisfaction=float(avg_satisfaction) if avg_satisfaction else None,
            top_questions=[
                TopQuestionStat(question=row[0] or "", count=row[1]) for row in top_questions_rows
            ],
            top_discussed_products=[
                ProductDiscussionStat(
                    product_id=row[0],
                    product_title=row[1],
                    mention_count=row[2],
                )
                for row in product_rows
            ],
        )

    async def list_conversations(
        self,
        db: AsyncSession,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ChatConversation], int]:
        total = int(
            (await db.execute(select(func.count()).select_from(ChatConversation))).scalar_one(),
        )
        offset = (page - 1) * page_size
        stmt = (
            select(ChatConversation)
            .options(selectinload(ChatConversation.messages))
            .order_by(desc(ChatConversation.updated_at))
            .offset(offset)
            .limit(page_size)
        )
        conversations = (await db.execute(stmt)).scalars().all()
        return list(conversations), total


def get_chat_service() -> ChatService:
    llm = LLMService()
    vector = VectorSearchService(llm)
    return ChatService(llm, vector)
