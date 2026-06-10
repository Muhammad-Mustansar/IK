from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_schemas import StorePolicyCreate, StorePolicyResponse, StorePolicyUpdate
from app.database import get_db
from app.dependencies import get_admin_user
from app.exceptions import ConflictError, NotFoundError
from app.faq_models import StorePolicy
from app.llm_service import LLMService
from app.models import User
from app.vector_search import VectorSearchService

router = APIRouter(prefix="/chatbot/policies", tags=["chatbot-policies"])


async def _sync_policy_embedding(db: AsyncSession, policy: StorePolicy) -> None:
    from app.config import get_settings

    if not get_settings().openai_api_key:
        return
    vector = VectorSearchService(LLMService())
    embedding = await vector.embed_query(f"{policy.title}\n{policy.content}")
    embedding_literal = "[" + ",".join(str(value) for value in embedding) + "]"
    await db.execute(
        text(
            """
            UPDATE store_policies
            SET embedding = CAST(:embedding AS vector), updated_at = NOW()
            WHERE id = :policy_id
            """,
        ),
        {"embedding": embedding_literal, "policy_id": policy.id},
    )


@router.get("", response_model=list[StorePolicyResponse])
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> list[StorePolicy]:
    return list((await db.execute(select(StorePolicy))).scalars().all())


@router.post("", response_model=StorePolicyResponse, status_code=201)
async def create_policy(
    payload: StorePolicyCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> StorePolicy:
    existing = await db.execute(
        select(StorePolicy).where(StorePolicy.policy_type == payload.policy_type),
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("Policy type already exists")
    policy = StorePolicy(**payload.model_dump())
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    await _sync_policy_embedding(db, policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.put("/{policy_id}", response_model=StorePolicyResponse)
async def update_policy(
    policy_id: int,
    payload: StorePolicyUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> StorePolicy:
    result = await db.execute(select(StorePolicy).where(StorePolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise NotFoundError("Policy not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    await db.commit()
    await db.refresh(policy)
    await _sync_policy_embedding(db, policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_admin_user)],
) -> None:
    result = await db.execute(select(StorePolicy).where(StorePolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise NotFoundError("Policy not found")
    await db.execute(delete(StorePolicy).where(StorePolicy.id == policy_id))
    await db.commit()
