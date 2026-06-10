from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.faq_models import PolicyType


class StorePolicyCreate(BaseModel):
    policy_type: PolicyType
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=3, max_length=10000)


class StorePolicyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=3, max_length=10000)


class StorePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    policy_type: PolicyType
    title: str
    content: str
    updated_at: datetime


class ProductSpecificationCreate(BaseModel):
    spec_key: str = Field(..., min_length=1, max_length=255)
    spec_value: str = Field(..., min_length=1, max_length=2000)


class ProductSpecificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    spec_key: str
    spec_value: str


class ProductPromotionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00"))
    discount_amount: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    is_active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ProductPromotionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00"))
    discount_amount: Decimal | None = None
    is_active: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ProductPromotionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    title: str
    description: str | None
    discount_percent: Decimal | None
    discount_amount: Decimal | None
    is_active: bool
    starts_at: datetime | None
    ends_at: datetime | None
