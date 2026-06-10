from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestError, NotFoundError
from app.models import Category, Product


async def ensure_category_exists(db: AsyncSession, category_id: int) -> None:
    result = await db.execute(select(Category.id).where(Category.id == category_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Category not found")


async def ensure_product_exists(db: AsyncSession, product_id: int) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")
    return product


async def validate_stock_available(product: Product, quantity: int) -> None:
    if quantity < 1:
        raise BadRequestError("Quantity must be at least 1")
    if product.stock_quantity < quantity:
        raise BadRequestError(
            "Insufficient stock",
            details={
                "product_id": product.id,
                "available": product.stock_quantity,
                "requested": quantity,
            },
        )
