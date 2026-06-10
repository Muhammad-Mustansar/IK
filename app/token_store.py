import uuid

from app.config import get_settings
from app.redis_client import get_redis


class RefreshTokenStore:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _key(self, jti: str) -> str:
        return f"refresh_token:{jti}"

    async def store(self, jti: str, user_id: str) -> None:
        redis = get_redis()
        ttl = self._settings.refresh_token_expire_days * 24 * 60 * 60
        await redis.set(self._key(jti), user_id, ex=ttl)

    async def is_valid(self, jti: str, user_id: str) -> bool:
        redis = get_redis()
        stored = await redis.get(self._key(jti))
        return stored == user_id

    async def revoke(self, jti: str) -> None:
        redis = get_redis()
        await redis.delete(self._key(jti))

    @staticmethod
    def new_jti() -> str:
        return str(uuid.uuid4())


def get_refresh_token_store() -> RefreshTokenStore:
    return RefreshTokenStore()
