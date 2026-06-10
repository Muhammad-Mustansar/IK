import os

import pytest

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-suite-32c")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SEED_POLICIES_ON_STARTUP", "false")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
