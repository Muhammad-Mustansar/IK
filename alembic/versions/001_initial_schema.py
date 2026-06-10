"""Initial schema with pgvector indexes.

Revision ID: 001
Revises:
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            email VARCHAR(320) NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'customer',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(255) NOT NULL
        )
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_categories_slug ON categories (slug)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            price NUMERIC(10, 2) NOT NULL,
            stock_quantity INTEGER NOT NULL DEFAULT 0,
            image_url VARCHAR(2048),
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_products_category_id ON products (category_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            total_amount NUMERIC(10, 2) NOT NULL,
            payment_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            shipping_status VARCHAR(20) NOT NULL DEFAULT 'processing',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            quantity INTEGER NOT NULL,
            price NUMERIC(10, 2) NOT NULL
        )
        """,
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_items_order_id ON order_items (order_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_items_product_id ON order_items (product_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS faqs (
            id SERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            category VARCHAR(100) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            embedding vector(1536),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_faqs_category ON faqs (category)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS store_policies (
            id SERIAL PRIMARY KEY,
            policy_type VARCHAR(20) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1536),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_specifications (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            spec_key VARCHAR(255) NOT NULL,
            spec_value TEXT NOT NULL
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_product_specifications_product_id "
        "ON product_specifications (product_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_promotions (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            discount_percent NUMERIC(5, 2),
            discount_amount NUMERIC(10, 2),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            starts_at TIMESTAMPTZ,
            ends_at TIMESTAMPTZ
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_product_promotions_product_id "
        "ON product_promotions (product_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_embeddings (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL UNIQUE REFERENCES products(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id VARCHAR(128) NOT NULL,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            satisfaction_score INTEGER,
            converted_to_order BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_conversations_session_id "
        "ON chat_conversations (session_id)",
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_conversations_user_id "
        "ON chat_conversations (user_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            confidence_score DOUBLE PRECISION,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id "
        "ON chat_messages (conversation_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id SERIAL PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            subject VARCHAR(255) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'escalated',
            confidence_score DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_conversation_id "
        "ON support_tickets (conversation_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_analytics_events (
            id SERIAL PRIMARY KEY,
            event_type VARCHAR(30) NOT NULL,
            conversation_id UUID REFERENCES chat_conversations(id) ON DELETE SET NULL,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            question_text TEXT,
            event_metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_analytics_events_event_type "
        "ON chat_analytics_events (event_type)",
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_analytics_created_at "
        "ON chat_analytics_events (created_at)",
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_product_embeddings_hnsw
        ON product_embeddings USING hnsw (embedding vector_cosine_ops)
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_faqs_hnsw
        ON faqs USING hnsw (embedding vector_cosine_ops)
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_store_policies_hnsw
        ON store_policies USING hnsw (embedding vector_cosine_ops)
        """,
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_analytics_events CASCADE")
    op.execute("DROP TABLE IF EXISTS support_tickets CASCADE")
    op.execute("DROP TABLE IF EXISTS chat_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS chat_conversations CASCADE")
    op.execute("DROP TABLE IF EXISTS product_embeddings CASCADE")
    op.execute("DROP TABLE IF EXISTS product_promotions CASCADE")
    op.execute("DROP TABLE IF EXISTS product_specifications CASCADE")
    op.execute("DROP TABLE IF EXISTS store_policies CASCADE")
    op.execute("DROP TABLE IF EXISTS faqs CASCADE")
    op.execute("DROP TABLE IF EXISTS order_items CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
    op.execute("DROP TABLE IF EXISTS products CASCADE")
    op.execute("DROP TABLE IF EXISTS categories CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
