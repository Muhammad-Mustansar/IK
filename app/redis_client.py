import json
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

_redis: aioredis.Redis | None = None


async def connect_redis() -> None:
    global _redis
    settings = get_settings()
    _redis = aioredis.from_url(
        str(settings.redis_url),
        encoding="utf-8",
        decode_responses=True,
    )


async def disconnect_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis client is not initialized")
    return _redis


class CartRepository:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._settings = get_settings()

    def _cart_key(self, identity: str) -> str:
        return f"cart:{identity}"

    async def get_items(self, identity: str) -> list[dict[str, int]]:
        raw = await self._redis.get(self._cart_key(identity))
        if not raw:
            return []
        data: list[dict[str, Any]] = json.loads(raw)
        return [
            {"product_id": int(item["product_id"]), "quantity": int(item["quantity"])}
            for item in data
        ]

    async def save_items(self, identity: str, items: list[dict[str, int]]) -> None:
        await self._redis.set(
            self._cart_key(identity),
            json.dumps(items),
            ex=self._settings.cart_ttl_seconds,
        )

    async def clear(self, identity: str) -> None:
        await self._redis.delete(self._cart_key(identity))

    @staticmethod
    def merge_item(
        items: list[dict[str, int]],
        product_id: int,
        quantity: int,
    ) -> list[dict[str, int]]:
        updated = False
        result: list[dict[str, int]] = []
        for item in items:
            if item["product_id"] == product_id:
                result.append({"product_id": product_id, "quantity": quantity})
                updated = True
            else:
                result.append(item)
        if not updated:
            result.append({"product_id": product_id, "quantity": quantity})
        return result

    @staticmethod
    def add_item(
        items: list[dict[str, int]],
        product_id: int,
        quantity: int,
    ) -> list[dict[str, int]]:
        updated = False
        result: list[dict[str, int]] = []
        for item in items:
            if item["product_id"] == product_id:
                result.append(
                    {"product_id": product_id, "quantity": item["quantity"] + quantity},
                )
                updated = True
            else:
                result.append(item)
        if not updated:
            result.append({"product_id": product_id, "quantity": quantity})
        return result

    @staticmethod
    def remove_item(items: list[dict[str, int]], product_id: int) -> list[dict[str, int]]:
        return [item for item in items if item["product_id"] != product_id]
