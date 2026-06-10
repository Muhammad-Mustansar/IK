from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.faq_models import ProductEmbedding, ProductPromotion, ProductSpecification
from app.llm_service import LLMService
from app.models import Category, Product
from app.vector_search import VectorSearchService


async def _load_product_metadata(
    db: AsyncSession,
    product_id: int,
) -> tuple[Product, str, dict[str, str], list[str]] | None:
    stmt = (
        select(Product, Category.name)
        .join(Category, Product.category_id == Category.id)
        .where(Product.id == product_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    product, category_name = row

    spec_rows = (
        await db.execute(
            select(ProductSpecification).where(ProductSpecification.product_id == product_id),
        )
    ).scalars().all()
    specifications = {spec.spec_key: spec.spec_value for spec in spec_rows}

    promo_rows = (
        await db.execute(
            select(ProductPromotion).where(
                ProductPromotion.product_id == product_id,
                ProductPromotion.is_active.is_(True),
            ),
        )
    ).scalars().all()
    promotions = [promo.title for promo in promo_rows]

    return product, category_name, specifications, promotions


async def sync_product_embedding(db: AsyncSession, product_id: int) -> None:
    if not get_settings().openai_api_key:
        return
    metadata = await _load_product_metadata(db, product_id)
    if metadata is None:
        return
    product, category_name, specifications, promotions = metadata
    vector = VectorSearchService(LLMService())
    await vector.upsert_product_embedding(
        db,
        product,
        category_name,
        specifications,
        promotions,
    )


async def delete_product_embedding(db: AsyncSession, product_id: int) -> None:
    await db.execute(delete(ProductEmbedding).where(ProductEmbedding.product_id == product_id))
    await db.commit()
