from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.faq_models import FAQ, ProductEmbedding, ProductPromotion, ProductSpecification
from app.llm_service import LLMService
from app.models import Category, Product


@dataclass
class RetrievedDocument:
    source_type: str
    source_id: int | str
    title: str
    content: str
    score: float


class VectorSearchService:
    def __init__(self, llm: LLMService) -> None:
        self._llm = llm

    async def embed_query(self, query: str) -> list[float]:
        return await self._llm.create_embedding(query)

    async def search_products(
        self,
        db: AsyncSession,
        query: str,
        *,
        limit: int = 5,
        max_price: Decimal | None = None,
        category_id: int | None = None,
    ) -> list[RetrievedDocument]:
        embedding = await self.embed_query(query)
        embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"

        filters = ["pe.embedding IS NOT NULL"]
        params: dict[str, object] = {"limit": limit, "embedding": embedding_literal}

        if max_price is not None:
            filters.append("p.price <= :max_price")
            params["max_price"] = max_price
        if category_id is not None:
            filters.append("p.category_id = :category_id")
            params["category_id"] = category_id

        where_clause = " AND ".join(filters)
        sql = text(
            f"""
            SELECT
                p.id,
                p.title,
                pe.content,
                1 - (pe.embedding <=> CAST(:embedding AS vector)) AS score
            FROM product_embeddings pe
            JOIN products p ON p.id = pe.product_id
            WHERE {where_clause}
            ORDER BY pe.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """,
        )
        rows = (await db.execute(sql, params)).mappings().all()
        if rows:
            return [
                RetrievedDocument(
                    source_type="product",
                    source_id=row["id"],
                    title=row["title"],
                    content=row["content"],
                    score=float(row["score"]),
                )
                for row in rows
            ]
        return await self._search_products_keyword(
            db,
            query,
            limit=limit,
            max_price=max_price,
            category_id=category_id,
        )

    async def _search_products_keyword(
        self,
        db: AsyncSession,
        query: str,
        *,
        limit: int,
        max_price: Decimal | None,
        category_id: int | None,
    ) -> list[RetrievedDocument]:
        stmt = (
            select(Product, Category.name)
            .join(Category, Product.category_id == Category.id)
            .where(Product.title.ilike(f"%{query}%"))
        )
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)
        if category_id is not None:
            stmt = stmt.where(Product.category_id == category_id)
        stmt = stmt.limit(limit)
        rows = (await db.execute(stmt)).all()
        documents: list[RetrievedDocument] = []
        for product, category_name in rows:
            content = (
                f"Product: {product.title}\nCategory: {category_name}\n"
                f"Price: {product.price}\nStock: {product.stock_quantity}\n"
                f"Description: {product.description or 'N/A'}"
            )
            documents.append(
                RetrievedDocument(
                    source_type="product",
                    source_id=product.id,
                    title=product.title,
                    content=content,
                    score=0.5,
                ),
            )
        return documents

    async def search_faqs(
        self,
        db: AsyncSession,
        query: str,
        *,
        limit: int = 5,
    ) -> list[RetrievedDocument]:
        embedding = await self.embed_query(query)
        embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        sql = text(
            """
            SELECT id, question, answer,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM faqs
            WHERE is_active = true AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """,
        )
        rows = (
            await db.execute(sql, {"embedding": embedding_literal, "limit": limit})
        ).mappings().all()
        return [
            RetrievedDocument(
                source_type="faq",
                source_id=row["id"],
                title=row["question"],
                content=row["answer"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def search_policies(
        self,
        db: AsyncSession,
        query: str,
        *,
        limit: int = 3,
    ) -> list[RetrievedDocument]:
        embedding = await self.embed_query(query)
        embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        sql = text(
            """
            SELECT id, title, content, policy_type,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM store_policies
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """,
        )
        rows = (
            await db.execute(sql, {"embedding": embedding_literal, "limit": limit})
        ).mappings().all()
        return [
            RetrievedDocument(
                source_type="policy",
                source_id=row["policy_type"],
                title=row["title"],
                content=row["content"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def hybrid_search(
        self,
        db: AsyncSession,
        query: str,
        *,
        max_price: Decimal | None = None,
        category_id: int | None = None,
    ) -> list[RetrievedDocument]:
        products = await self.search_products(
            db,
            query,
            limit=5,
            max_price=max_price,
            category_id=category_id,
        )
        faqs = await self.search_faqs(db, query, limit=3)
        policies = await self.search_policies(db, query, limit=2)
        combined = products + faqs + policies
        combined.sort(key=lambda doc: doc.score, reverse=True)
        return combined

    async def upsert_product_embedding(
        self,
        db: AsyncSession,
        product: Product,
        category_name: str,
        specifications: dict[str, str],
        promotions: list[str],
    ) -> None:
        promo_text = "; ".join(promotions) if promotions else "None"
        spec_text = "; ".join(f"{key}: {value}" for key, value in specifications.items())
        content = (
            f"Product: {product.title}\n"
            f"Category: {category_name}\n"
            f"Price: {product.price}\n"
            f"Stock: {product.stock_quantity}\n"
            f"Description: {product.description or 'N/A'}\n"
            f"Specifications: {spec_text or 'N/A'}\n"
            f"Promotions: {promo_text}\n"
            f"Image: {product.image_url or 'N/A'}"
        )
        embedding = await self._llm.create_embedding(content)
        embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"

        existing = await db.execute(
            select(ProductEmbedding).where(ProductEmbedding.product_id == product.id),
        )
        record = existing.scalar_one_or_none()
        if record is None:
            record = ProductEmbedding(product_id=product.id, content=content, embedding=embedding)
            db.add(record)
        else:
            record.content = content
            await db.execute(
                text(
                    """
                    UPDATE product_embeddings
                    SET content = :content, embedding = CAST(:embedding AS vector), updated_at = NOW()
                    WHERE product_id = :product_id
                    """,
                ),
                {
                    "content": content,
                    "embedding": embedding_literal,
                    "product_id": product.id,
                },
            )
        await db.commit()

    async def upsert_faq_embedding(self, db: AsyncSession, faq: FAQ) -> None:
        text_content = f"{faq.question}\n{faq.answer}"
        embedding = await self._llm.create_embedding(text_content)
        embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"
        await db.execute(
            text(
                """
                UPDATE faqs
                SET embedding = CAST(:embedding AS vector), updated_at = NOW()
                WHERE id = :faq_id
                """,
            ),
            {"embedding": embedding_literal, "faq_id": faq.id},
        )
        await db.commit()

    async def load_product_context(
        self,
        db: AsyncSession,
        product_ids: list[int],
    ) -> list[dict[str, object]]:
        if not product_ids:
            return []
        stmt = (
            select(Product, Category.name)
            .join(Category, Product.category_id == Category.id)
            .where(Product.id.in_(product_ids))
        )
        rows = (await db.execute(stmt)).all()
        result: list[dict[str, object]] = []
        for product, category_name in rows:
            spec_rows = (
                await db.execute(
                    select(ProductSpecification).where(
                        ProductSpecification.product_id == product.id,
                    ),
                )
            ).scalars().all()
            specifications = {spec.spec_key: spec.spec_value for spec in spec_rows}

            promo_rows = (
                await db.execute(
                    select(ProductPromotion).where(
                        ProductPromotion.product_id == product.id,
                        ProductPromotion.is_active.is_(True),
                    ),
                )
            ).scalars().all()
            promo_text = ", ".join(promo.title for promo in promo_rows) if promo_rows else None
            result.append(
                {
                    "id": product.id,
                    "title": product.title,
                    "price": product.price,
                    "stock_quantity": product.stock_quantity,
                    "image_url": product.image_url,
                    "category_name": category_name,
                    "description": product.description,
                    "specifications": specifications,
                    "promotion": promo_text,
                },
            )
        return result
