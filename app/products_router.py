import math
from decimal import Decimal
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_admin_user
from app.exceptions import NotFoundError
from app.models import Product, User
from app.product_embedding_service import delete_product_embedding, sync_product_embedding
from app.schemas import ProductCreate, ProductResponse
from app.validators import ensure_category_exists

router = APIRouter(prefix="/products", tags=["products"])


class ProductSortField(str, Enum):
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"
    NAME = "name"


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class PaginatedProductResponse(BaseModel):
    items: list[ProductResponse]
    meta: PaginationMeta


class ProductUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=Decimal("0.00"), decimal_places=2)
    stock_quantity: int | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=2048)
    category_id: int | None = None


@router.get("", response_model=PaginatedProductResponse)
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=255)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    sort: ProductSortField = ProductSortField.NEWEST,
) -> PaginatedProductResponse:
    filters = []
    if search:
        filters.append(Product.title.ilike(f"%{search}%"))
    if category_id is not None:
        filters.append(Product.category_id == category_id)

    count_stmt = select(func.count()).select_from(Product)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = select(Product)
    if filters:
        stmt = stmt.where(*filters)

    if sort == ProductSortField.PRICE_ASC:
        stmt = stmt.order_by(asc(Product.price))
    elif sort == ProductSortField.PRICE_DESC:
        stmt = stmt.order_by(desc(Product.price))
    elif sort == ProductSortField.NAME:
        stmt = stmt.order_by(asc(Product.title))
    else:
        stmt = stmt.order_by(desc(Product.created_at))

    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    products = (await db.execute(stmt)).scalars().all()

    total_pages = math.ceil(total_items / page_size) if total_items else 0
    return PaginatedProductResponse(
        items=[ProductResponse.model_validate(product) for product in products],
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1 and total_pages > 0,
        ),
    )


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")
    return product


@router.post("", response_model=ProductResponse, status_code=201)
async def create_product(
    payload: ProductCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> Product:
    await ensure_category_exists(db, payload.category_id)
    product = Product(**payload.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    await sync_product_embedding(db, product.id)
    return product


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")

    updates = payload.model_dump(exclude_unset=True)
    if "category_id" in updates and updates["category_id"] is not None:
        await ensure_category_exists(db, updates["category_id"])

    for field, value in updates.items():
        setattr(product, field, value)

    await db.commit()
    await db.refresh(product)
    await sync_product_embedding(db, product.id)
    return product


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> None:
    result = await db.execute(select(Product).where(Product.id == product_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Product not found")
    await delete_product_embedding(db, product_id)
    await db.execute(delete(Product).where(Product.id == product_id))
    await db.commit()
