from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_schemas import ProductSpecificationCreate, ProductSpecificationResponse
from app.database import get_db
from app.dependencies import get_admin_user
from app.exceptions import NotFoundError
from app.faq_models import ProductSpecification
from app.models import User
from app.product_embedding_service import sync_product_embedding
from app.validators import ensure_product_exists

router = APIRouter(prefix="/products/{product_id}/specifications", tags=["product-specifications"])


@router.get("", response_model=list[ProductSpecificationResponse])
async def list_specifications(
    product_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProductSpecification]:
    await ensure_product_exists(db, product_id)
    result = await db.execute(
        select(ProductSpecification).where(ProductSpecification.product_id == product_id),
    )
    return list(result.scalars().all())


@router.post("", response_model=ProductSpecificationResponse, status_code=201)
async def create_specification(
    product_id: int,
    payload: ProductSpecificationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> ProductSpecification:
    await ensure_product_exists(db, product_id)
    spec = ProductSpecification(product_id=product_id, **payload.model_dump())
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    await sync_product_embedding(db, product_id)
    return spec


@router.delete("/{spec_id}", status_code=204)
async def delete_specification(
    product_id: int,
    spec_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> None:
    result = await db.execute(
        select(ProductSpecification).where(
            ProductSpecification.id == spec_id,
            ProductSpecification.product_id == product_id,
        ),
    )
    spec = result.scalar_one_or_none()
    if spec is None:
        raise NotFoundError("Specification not found")
    await db.execute(
        delete(ProductSpecification).where(ProductSpecification.id == spec_id),
    )
    await db.commit()
    await sync_product_embedding(db, product_id)
