from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_schemas import (
    ProductPromotionCreate,
    ProductPromotionResponse,
    ProductPromotionUpdate,
)
from app.database import get_db
from app.dependencies import get_admin_user
from app.exceptions import NotFoundError
from app.faq_models import ProductPromotion
from app.models import User
from app.product_embedding_service import sync_product_embedding
from app.validators import ensure_product_exists

router = APIRouter(prefix="/products/{product_id}/promotions", tags=["product-promotions"])


@router.get("", response_model=list[ProductPromotionResponse])
async def list_promotions(
    product_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProductPromotion]:
    await ensure_product_exists(db, product_id)
    result = await db.execute(
        select(ProductPromotion).where(ProductPromotion.product_id == product_id),
    )
    return list(result.scalars().all())


@router.post("", response_model=ProductPromotionResponse, status_code=201)
async def create_promotion(
    product_id: int,
    payload: ProductPromotionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> ProductPromotion:
    await ensure_product_exists(db, product_id)
    promotion = ProductPromotion(product_id=product_id, **payload.model_dump())
    db.add(promotion)
    await db.commit()
    await db.refresh(promotion)
    await sync_product_embedding(db, product_id)
    return promotion


@router.put("/{promotion_id}", response_model=ProductPromotionResponse)
async def update_promotion(
    product_id: int,
    promotion_id: int,
    payload: ProductPromotionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> ProductPromotion:
    result = await db.execute(
        select(ProductPromotion).where(
            ProductPromotion.id == promotion_id,
            ProductPromotion.product_id == product_id,
        ),
    )
    promotion = result.scalar_one_or_none()
    if promotion is None:
        raise NotFoundError("Promotion not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(promotion, field, value)
    await db.commit()
    await db.refresh(promotion)
    await sync_product_embedding(db, product_id)
    return promotion


@router.delete("/{promotion_id}", status_code=204)
async def delete_promotion(
    product_id: int,
    promotion_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> None:
    result = await db.execute(
        select(ProductPromotion).where(
            ProductPromotion.id == promotion_id,
            ProductPromotion.product_id == product_id,
        ),
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Promotion not found")
    await db.execute(
        delete(ProductPromotion).where(ProductPromotion.id == promotion_id),
    )
    await db.commit()
    await sync_product_embedding(db, product_id)
