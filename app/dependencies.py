import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.exceptions import ForbiddenError, UnauthorizedError
from app.models import User, UserRole
from app.redis_client import CartRepository, get_redis
from app.security import TOKEN_TYPE_ACCESS, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    return await _resolve_user_from_token(token, db)


async def get_current_user_optional(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    if token is None:
        return None
    return await _resolve_user_from_token(token, db)


async def get_admin_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin privileges required")
    return current_user


async def _resolve_user_from_token(token: str, db: AsyncSession) -> User:
    try:
        payload = decode_token(token)
        if payload.get("type") != TOKEN_TYPE_ACCESS:
            raise UnauthorizedError("Invalid access token")
        user_id = payload.get("sub")
        if user_id is None:
            raise UnauthorizedError("Invalid token payload")
    except JWTError as exc:
        raise UnauthorizedError("Could not validate credentials") from exc

    result = await db.execute(select(User).where(User.id == uuid.UUID(str(user_id))))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User not found")
    return user


@dataclass(frozen=True)
class CartIdentity:
    key: str
    is_guest: bool
    session_token: str | None = None


async def get_cart_identity(
    request: Request,
    response: Response,
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    cart_session: Annotated[str | None, Header(alias="X-Cart-Session")] = None,
) -> CartIdentity:
    settings = get_settings()
    if current_user is not None:
        return CartIdentity(key=f"user:{current_user.id}", is_guest=False)

    session_token = cart_session or request.cookies.get(settings.cart_session_header)
    if session_token is None:
        session_token = str(uuid.uuid4())
        response.headers[settings.cart_session_header] = session_token

    return CartIdentity(
        key=f"guest:{session_token}",
        is_guest=True,
        session_token=session_token,
    )


def get_cart_repository() -> CartRepository:
    return CartRepository(get_redis())


async def resolve_user_from_token_optional(token: str, db: AsyncSession) -> User | None:
    try:
        return await _resolve_user_from_token(token, db)
    except UnauthorizedError:
        return None
