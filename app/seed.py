from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.faq_models import PolicyType, StorePolicy
from app.llm_service import LLMService
from app.vector_search import VectorSearchService

DEFAULT_POLICIES: list[dict[str, str]] = [
    {
        "policy_type": PolicyType.SHIPPING.value,
        "title": "Shipping Information",
        "content": (
            "We offer standard shipping (5-7 business days) and express shipping "
            "(2-3 business days). Free standard shipping is available on orders over $50."
        ),
    },
    {
        "policy_type": PolicyType.REFUND.value,
        "title": "Refund Policy",
        "content": (
            "Refunds are issued within 5-10 business days after we receive and inspect "
            "returned items. Original payment method is credited."
        ),
    },
    {
        "policy_type": PolicyType.RETURN.value,
        "title": "Return Policy",
        "content": (
            "Items may be returned within 30 days of delivery in original condition. "
            "Return shipping labels are provided for defective items."
        ),
    },
    {
        "policy_type": PolicyType.PAYMENT.value,
        "title": "Payment Methods",
        "content": (
            "We accept Visa, Mastercard, American Express, PayPal, and Apple Pay. "
            "All transactions are encrypted and PCI compliant."
        ),
    },
    {
        "policy_type": PolicyType.DELIVERY.value,
        "title": "Delivery Times",
        "content": (
            "Standard delivery: 5-7 business days. Express delivery: 2-3 business days. "
            "International delivery: 10-15 business days depending on destination."
        ),
    },
]


async def ensure_pgvector_extension(db: AsyncSession) -> None:
    await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await db.commit()


async def seed_store_policies(db: AsyncSession) -> None:
    existing = (await db.execute(select(StorePolicy.id).limit(1))).scalar_one_or_none()
    if existing is not None:
        return

    settings = get_settings()
    vector = VectorSearchService(LLMService()) if settings.openai_api_key else None
    for policy_data in DEFAULT_POLICIES:
        policy = StorePolicy(
            policy_type=PolicyType(policy_data["policy_type"]),
            title=policy_data["title"],
            content=policy_data["content"],
        )
        db.add(policy)
        await db.flush()
        if vector is not None:
            embedding = await vector.embed_query(f"{policy.title}\n{policy.content}")
            embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"
            await db.execute(
                text(
                    """
                    UPDATE store_policies
                    SET embedding = CAST(:embedding AS vector)
                    WHERE id = :policy_id
                    """,
                ),
                {"embedding": embedding_literal, "policy_id": policy.id},
            )
    await db.commit()
