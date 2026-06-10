from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CartIdentity, get_cart_identity, get_cart_repository
from app.exceptions import BadRequestError, NotFoundError
from app.models import Product
from app.redis_client import CartRepository

router = APIRouter(prefix="/cart", tags=["cart"])


class CartItemRequest(BaseModel):
    product_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1)


class CartRemoveRequest(BaseModel):
    product_id: int = Field(..., ge=1)


class CartProductInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    price: Decimal
    image_url: str | None
    stock_quantity: int


class CartItemResponse(BaseModel):
    product_id: int
    quantity: int
    product: CartProductInfo
    line_total: Decimal


class CartResponse(BaseModel):
    items: list[CartItemResponse]
    total_items: int
    subtotal: Decimal
    is_guest: bool
    session_token: str | None = None


async def _validate_stock(
    db: AsyncSession,
    product_id: int,
    quantity: int,
) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")
    if quantity < 0:
        raise BadRequestError("Quantity cannot be negative")
    if product.stock_quantity < quantity:
        raise BadRequestError(
            "Insufficient stock",
            details={
                "product_id": product_id,
                "available": product.stock_quantity,
                "requested": quantity,
            },
        )
    return product


async def _build_cart_response(
    db: AsyncSession,
    items: list[dict[str, int]],
    identity: CartIdentity,
) -> CartResponse:
    if not items:
        return CartResponse(
            items=[],
            total_items=0,
            subtotal=Decimal("0.00"),
            is_guest=identity.is_guest,
            session_token=identity.session_token,
        )

    product_ids = [item["product_id"] for item in items]
    result = await db.execute(select(Product).where(Product.id.in_(product_ids)))
    products = {product.id: product for product in result.scalars().all()}

    response_items: list[CartItemResponse] = []
    subtotal = Decimal("0.00")
    total_items = 0

    for item in items:
        product = products.get(item["product_id"])
        if product is None:
            continue
        line_total = product.price * item["quantity"]
        subtotal += line_total
        total_items += item["quantity"]
        response_items.append(
            CartItemResponse(
                product_id=item["product_id"],
                quantity=item["quantity"],
                product=CartProductInfo.model_validate(product),
                line_total=line_total,
            ),
        )

    return CartResponse(
        items=response_items,
        total_items=total_items,
        subtotal=subtotal,
        is_guest=identity.is_guest,
        session_token=identity.session_token,
    )


@router.post("/add", response_model=CartResponse)
async def add_to_cart(
    payload: CartItemRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> CartResponse:
    product = await _validate_stock(db, payload.product_id, payload.quantity)
    items = await cart_repo.get_items(identity.key)
    existing_qty = next(
        (item["quantity"] for item in items if item["product_id"] == payload.product_id),
        0,
    )
    new_qty = existing_qty + payload.quantity
    if product.stock_quantity < new_qty:
        raise BadRequestError(
            "Insufficient stock",
            details={
                "product_id": payload.product_id,
                "available": product.stock_quantity,
                "requested": new_qty,
            },
        )
    updated = CartRepository.add_item(items, payload.product_id, payload.quantity)
    await cart_repo.save_items(identity.key, updated)
    return await _build_cart_response(db, updated, identity)


@router.post("/update", response_model=CartResponse)
async def update_cart_item(
    payload: CartItemRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> CartResponse:
    await _validate_stock(db, payload.product_id, payload.quantity)
    items = await cart_repo.get_items(identity.key)
    updated = CartRepository.merge_item(items, payload.product_id, payload.quantity)
    await cart_repo.save_items(identity.key, updated)
    return await _build_cart_response(db, updated, identity)


@router.post("/remove", response_model=CartResponse)
async def remove_from_cart(
    payload: CartRemoveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> CartResponse:
    items = await cart_repo.get_items(identity.key)
    updated = CartRepository.remove_item(items, payload.product_id)
    await cart_repo.save_items(identity.key, updated)
    return await _build_cart_response(db, updated, identity)


@router.get("", response_model=CartResponse)
async def get_cart(
    db: Annotated[AsyncSession, Depends(get_db)],
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> CartResponse:
    items = await cart_repo.get_items(identity.key)
    if not items:
        await cart_repo.save_items(identity.key, [])
    return await _build_cart_response(db, items, identity)


@router.delete("/clear", status_code=204)
async def clear_cart(
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> None:
    await cart_repo.clear(identity.key)
