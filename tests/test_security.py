from app.security import hash_password, verify_password


def test_password_hash_and_verify() -> None:
    hashed = hash_password("securepassword123")
    assert hashed != "securepassword123"
    assert verify_password("securepassword123", hashed)
    assert not verify_password("wrongpassword", hashed)
