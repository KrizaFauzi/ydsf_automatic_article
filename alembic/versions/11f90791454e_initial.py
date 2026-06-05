"""initial — create all tables matching SQLModel definitions

Revision ID: 11f90791454e
Revises: 
Create Date: 2026-05-28 08:01:50.537768

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "11f90791454e"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all application tables."""

    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("is_verified", sa.Boolean(), default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "deleted_at", sa.DateTime(), nullable=True
        ),  # soft delete
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    # ── chat_sessions ──────────────────────────────────────────────────────────
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), nullable=False, index=True
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("vector_db_path", sa.String(500), nullable=False),
        sa.Column("model_choice", sa.String(100), nullable=True),
        sa.Column("is_ready", sa.Boolean(), default=False, nullable=False),
        sa.Column(
            "processing_status",
            sa.String(20),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("progress_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    # ── chat_messages ──────────────────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    # ── article_sources ────────────────────────────────────────────────────────
    op.create_table(
        "article_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False, index=True),
        sa.Column("judul", sa.Text(), nullable=False),
        sa.Column("sumber", sa.String(100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def downgrade() -> None:
    """Drop all application tables (reverse order for FK constraints)."""
    op.drop_table("article_sources")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("users")
