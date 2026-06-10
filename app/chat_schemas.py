from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WSMessageType(str, Enum):
    TYPING = "typing"
    CHUNK = "chunk"
    COMPLETE = "complete"
    ERROR = "error"
    ESCALATION = "escalation"
    PING = "ping"
    PONG = "pong"


class ChatWSIncoming(BaseModel):
    type: Literal["message", "ping", "satisfaction"] = "message"
    content: str | None = None
    session_id: str | None = None
    product_id: int | None = None
    score: int | None = Field(default=None, ge=1, le=5)


class ChatWSOutgoing(BaseModel):
    type: WSMessageType
    content: str | None = None
    active: bool | None = None
    session_id: str | None = None
    conversation_id: UUID | None = None
    confidence: float | None = None
    products: list["ProductSnippet"] | None = None
    recommendations: list["ProductSnippet"] | None = None
    ticket_id: int | None = None
    message: str | None = None


class ProductSnippet(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    price: Decimal
    stock_quantity: int
    image_url: str | None = None
    category_name: str | None = None
    description: str | None = None
    specifications: dict[str, str] | None = None
    promotion: str | None = None


class ChatMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    role: str
    content: str
    confidence_score: float | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    created_at: datetime


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: str
    user_id: UUID | None = None
    satisfaction_score: int | None = None
    converted_to_order: bool
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ChatMessageSchema] = []


class FAQCreate(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    answer: str = Field(..., min_length=3, max_length=10000)
    category: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True


class FAQUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=3, max_length=2000)
    answer: str | None = Field(default=None, min_length=3, max_length=10000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class FAQResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answer: str
    category: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SatisfactionRequest(BaseModel):
    session_id: str
    score: int = Field(..., ge=1, le=5)


class TopQuestionStat(BaseModel):
    question: str
    count: int


class ProductDiscussionStat(BaseModel):
    product_id: int
    product_title: str
    mention_count: int


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    product_id: int | None = Field(default=None, ge=1)


class ChatMessageResponse(BaseModel):
    answer: str
    confidence: float
    conversation_id: UUID
    session_id: str
    products: list[ProductSnippet] = []
    recommendations: list[ProductSnippet] = []
    ticket_id: int | None = None


class ChatAnalyticsResponse(BaseModel):
    total_conversations: int
    total_questions: int
    escalation_count: int
    conversion_rate: float
    average_satisfaction: float | None
    top_questions: list[TopQuestionStat]
    top_discussed_products: list[ProductDiscussionStat]
