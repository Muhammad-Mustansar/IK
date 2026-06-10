from app.redis_client import CartRepository, get_redis


async def merge_guest_cart_into_user(
    guest_session_token: str,
    user_id: str,
) -> None:
    cart_repo = CartRepository(get_redis())
    guest_key = f"guest:{guest_session_token}"
    user_key = f"user:{user_id}"

    guest_items = await cart_repo.get_items(guest_key)
    if not guest_items:
        return

    user_items = await cart_repo.get_items(user_key)
    merged = list(user_items)

    for item in guest_items:
        existing = next(
            (entry for entry in merged if entry["product_id"] == item["product_id"]),
            None,
        )
        if existing is None:
            merged.append(item)
        else:
            existing["quantity"] += item["quantity"]

    await cart_repo.save_items(user_key, merged)
    await cart_repo.clear(guest_key)
