import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

import app.faq_models  # noqa: F401
from app.auth_router import router as auth_router
from app.cart_router import router as cart_router
from app.categories_router import router as categories_router
from app.chat_router import chatbot_router, router as chat_router
from app.config import get_settings
from app.database import async_session_factory, engine, get_db
from app.exceptions import register_exception_handlers
from app.logging_config import setup_logging
from app.orders_router import router as orders_router
from app.policies_router import router as policies_router
from app.products_router import router as products_router
from app.promotions_router import router as promotions_router
from app.redis_client import connect_redis, disconnect_redis, get_redis
from app.seed import ensure_pgvector_extension, seed_store_policies
from app.specifications_router import router as specifications_router

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        redis = get_redis()
        window = int(time.time()) // settings.rate_limit_window_seconds
        key = f"rate_limit:{client_ip}:{window}"

        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, settings.rate_limit_window_seconds)

        if current > settings.rate_limit_requests:
            return Response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content='{"detail":"Rate limit exceeded"}',
                media_type="application/json",
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
        response.headers["X-RateLimit-Remaining"] = str(
            max(settings.rate_limit_requests - current, 0),
        )
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    async with async_session_factory() as db:
        await ensure_pgvector_extension(db)
        if settings.seed_policies_on_startup:
            await seed_store_policies(db)

    await connect_redis()
    yield
    await disconnect_redis()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(RateLimitMiddleware)

    register_exception_handlers(app)

    app.include_router(auth_router)
    app.include_router(categories_router)
    app.include_router(products_router)
    app.include_router(specifications_router)
    app.include_router(promotions_router)
    app.include_router(cart_router)
    app.include_router(orders_router)
    app.include_router(chat_router)
    app.include_router(chatbot_router)
    app.include_router(policies_router)

    @app.get("/health")
    async def health(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        await get_redis().ping()
        return {
            "status": "ok",
            "database": "up",
            "redis": "up",
        }

    return app


app = create_app()
