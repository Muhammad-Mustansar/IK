from app.redis_client import CartRepository


def test_cart_merge_and_add() -> None:
    items = [{"product_id": 1, "quantity": 2}]
    added = CartRepository.add_item(items, 2, 3)
    assert {"product_id": 1, "quantity": 2} in added
    assert {"product_id": 2, "quantity": 3} in added

    merged = CartRepository.merge_item(added, 1, 5)
    product_one = next(item for item in merged if item["product_id"] == 1)
    assert product_one["quantity"] == 5

    removed = CartRepository.remove_item(merged, 2)
    assert all(item["product_id"] != 2 for item in removed)
