"""initial schema

Enables the pgvector extension, creates all tables from the app metadata, and
adds the search indexes (GIN full-text on doc_chunks.content and an IVFFlat
index on the embedding columns).

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-11
"""
from __future__ import annotations

from alembic import op

from app.config import settings
from app.database import Base
from app import models  # noqa: F401 - registers tables on the metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # pgvector must exist before the vector columns are created
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)

    # full-text search over chunk content (used by keyword retrieval / search)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_chunks_content_fts "
        "ON doc_chunks USING GIN (to_tsvector('english', content))"
    )
    # approximate-nearest-neighbour indexes for semantic retrieval
    lists = 100
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_doc_chunks_embedding "
        f"ON doc_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_kb_embedding "
        f"ON troubleshooting_kb USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_kb_embedding")
    op.execute("DROP INDEX IF EXISTS ix_doc_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS ix_doc_chunks_content_fts")
    Base.metadata.drop_all(bind=bind)
