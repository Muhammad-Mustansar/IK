"""Pydantic v2 schemas for request/response serialization."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import PaymentStatus, ShippingStatus, UserRole


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    role: UserRole = UserRole.CUSTOMER


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


class ProductBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    price: Decimal = Field(..., ge=Decimal("0.00"), decimal_places=2)
    stock_quantity: int = Field(..., ge=0)
    image_url: str | None = Field(default=None, max_length=2048)
    category_id: int


class ProductCreate(ProductBase):
    pass


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# OrderItem
# ---------------------------------------------------------------------------


class OrderItemBase(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)
    price: Decimal = Field(..., ge=Decimal("0.00"), decimal_places=2)


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)


class OrderItemResponse(OrderItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class OrderBase(BaseModel):
    total_amount: Decimal = Field(..., ge=Decimal("0.00"), decimal_places=2)
    payment_status: PaymentStatus = PaymentStatus.PENDING
    shipping_status: ShippingStatus = ShippingStatus.PROCESSING


class OrderCreate(BaseModel):
    items: list[OrderItemCreate] = Field(..., min_length=1)


class OrderResponse(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    created_at: datetime
    items: list[OrderItemResponse] = []


class OrderSummaryResponse(OrderBase):
    """Order without nested items — useful for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    created_at: datetime
