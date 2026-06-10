import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cart_service import merge_guest_cart_into_user
from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import ConflictError, UnauthorizedError
from app.models import User, UserRole
from app.schemas import UserCreate, UserResponse
from app.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.token_store import get_refresh_token_store

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    payload: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("Email already registered")

    user = User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.CUSTOMER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    cart_session: Annotated[str | None, Header(alias="X-Cart-Session")] = None,
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise UnauthorizedError("Incorrect email or password")

    if cart_session:
        await merge_guest_cart_into_user(cart_session, str(user.id))

    token_store = get_refresh_token_store()
    jti = token_store.new_jti()
    refresh_token = create_refresh_token(user.id, jti=jti)
    await token_store.store(jti, str(user.id))

    return TokenResponse(
        access_token=create_access_token(user.id, role=user.role.value),
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    try:
        token_payload = decode_token(payload.refresh_token)
        if token_payload.get("type") != TOKEN_TYPE_REFRESH:
            raise UnauthorizedError("Invalid refresh token")
        user_id = token_payload.get("sub")
        jti = token_payload.get("jti")
        if user_id is None or jti is None:
            raise UnauthorizedError("Invalid refresh token")
    except JWTError as exc:
        raise UnauthorizedError("Invalid refresh token") from exc

    token_store = get_refresh_token_store()
    if not await token_store.is_valid(jti, str(user_id)):
        raise UnauthorizedError("Refresh token revoked or expired")

    result = await db.execute(select(User).where(User.id == uuid.UUID(str(user_id))))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User not found")

    await token_store.revoke(jti)
    new_jti = token_store.new_jti()
    new_refresh = create_refresh_token(user.id, jti=new_jti)
    await token_store.store(new_jti, str(user.id))

    return TokenResponse(
        access_token=create_access_token(user.id, role=user.role.value),
        refresh_token=new_refresh,
    )


@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest) -> None:
    try:
        token_payload = decode_token(payload.refresh_token)
        jti = token_payload.get("jti")
        if jti:
            await get_refresh_token_store().revoke(jti)
    except JWTError:
        return


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user
