import logging
import math
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.conversion_service import mark_chat_conversion
from app.database import get_db
from app.dependencies import CartIdentity, get_admin_user, get_cart_identity, get_cart_repository, get_current_user
from app.exceptions import BadRequestError, InsufficientStockError, NotFoundError
from app.models import Order, OrderItem, PaymentStatus, Product, ShippingStatus, User, UserRole
from app.redis_client import CartRepository
from app.schemas import OrderResponse, OrderSummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class PaginatedOrderResponse(BaseModel):
    items: list[OrderSummaryResponse]
    meta: PaginationMeta


@router.get("", response_model=PaginatedOrderResponse)
async def list_my_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedOrderResponse:
    total_items = int(
        (
            await db.execute(
                select(func.count()).select_from(Order).where(Order.user_id == current_user.id),
            )
        ).scalar_one(),
    )
    offset = (page - 1) * page_size
    stmt = (
        select(Order)
        .where(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    orders = (await db.execute(stmt)).scalars().all()
    total_pages = math.ceil(total_items / page_size) if total_items else 0
    return PaginatedOrderResponse(
        items=[OrderSummaryResponse.model_validate(order) for order in orders],
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1 and total_pages > 0,
        ),
    )


@router.get("/admin", response_model=PaginatedOrderResponse)
async def list_all_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedOrderResponse:
    total_items = int((await db.execute(select(func.count()).select_from(Order))).scalar_one())
    offset = (page - 1) * page_size
    orders = (
        await db.execute(
            select(Order).order_by(Order.created_at.desc()).offset(offset).limit(page_size),
        )
    ).scalars().all()
    total_pages = math.ceil(total_items / page_size) if total_items else 0
    return PaginatedOrderResponse(
        items=[OrderSummaryResponse.model_validate(order) for order in orders],
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1 and total_pages > 0,
        ),
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Order:
    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id),
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise NotFoundError("Order not found")
    if current_user.role != UserRole.ADMIN and order.user_id != current_user.id:
        raise NotFoundError("Order not found")
    return order


@router.post("/checkout", response_model=OrderResponse, status_code=201)
async def checkout(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    identity: Annotated[CartIdentity, Depends(get_cart_identity)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
) -> Order:
    cart_items = await cart_repo.get_items(identity.key)
    if not cart_items:
        raise BadRequestError("Cart is empty")

    product_ids = sorted({item["product_id"] for item in cart_items})
    quantities = {item["product_id"]: item["quantity"] for item in cart_items}

    async with db.begin():
        result = await db.execute(
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id)
            .with_for_update(),
        )
        products = {product.id: product for product in result.scalars().all()}

        if len(products) != len(product_ids):
            raise BadRequestError("One or more products in cart no longer exist")

        total_amount = Decimal("0.00")
        order_items: list[OrderItem] = []

        for product_id in product_ids:
            product = products[product_id]
            quantity = quantities[product_id]
            if quantity <= 0:
                raise BadRequestError("Invalid cart quantity")
            if product.stock_quantity < quantity:
                raise InsufficientStockError(
                    f"Insufficient stock for product {product_id}",
                    details={
                        "product_id": product_id,
                        "available": product.stock_quantity,
                        "requested": quantity,
                    },
                )
            product.stock_quantity -= quantity
            total_amount += product.price * quantity
            order_items.append(
                OrderItem(
                    product_id=product.id,
                    quantity=quantity,
                    price=product.price,
                ),
            )

        order = Order(
            user_id=current_user.id,
            total_amount=total_amount,
            payment_status=PaymentStatus.PENDING,
            shipping_status=ShippingStatus.PROCESSING,
            items=order_items,
        )
        db.add(order)
        await db.flush()
        order_id = order.id

    await mark_chat_conversion(db, current_user.id)

    try:
        await cart_repo.clear(identity.key)
    except Exception:
        logger.exception("Failed to clear cart after successful checkout for order %s", order_id)

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id),
    )
    return result.scalar_one()
