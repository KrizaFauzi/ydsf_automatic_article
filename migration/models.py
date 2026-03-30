"""
Database Models menggunakan SQLModel (combines ORM & Pydantic schemas).

Models:
- User: Registered users dengan authentication info
- ChatSession: Conversation sessions dengan corresponding ChromaDB
- ChatMessage: Individual messages dalam chat session
"""

from typing import Optional
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import Text
import uuid


def utcnow():
    """Return current UTC time"""
    return datetime.now(timezone.utc)


# ─── User Model ──────────────────────────────────────────────────────────────


class User(SQLModel, table=True):
    """
    User model untuk authentication.
    
    Fields:
    - id: UUID primary key
    - username: Unique username
    - email: Unique email untuk login
    - hashed_password: bcrypt hashed password
    - is_active: Account activation status
    - is_verified: Email verification status
    - timestamps: created_at, updated_at, deleted_at (soft delete)
    """

    __tablename__ = "users"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
        description="UUID primary key",
    )
    username: str = Field(
        max_length=100,
        unique=True,
        index=True,
        description="Unique username",
    )
    email: str = Field(
        max_length=255,
        unique=True,
        index=True,
        description="Unique email untuk login",
    )
    hashed_password: str = Field(
        max_length=255,
        description="bcrypt hashed password",
    )
    is_active: bool = Field(
        default=True,
        description="Account activation status",
    )
    is_verified: bool = Field(
        default=False,
        description="Email verification status",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="Account creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        description="Last update timestamp",
    )
    deleted_at: Optional[datetime] = Field(
        default=None,
        description="Soft delete timestamp",
    )


# ─── ChatSession Model ────────────────────────────────────────────────────────


class ChatSession(SQLModel, table=True):
    """
    Chat session model untuk grouping related conversations.
    
    Setiap session memiliki:
    - Unique ChromaDB directory untuk vector storage
    - Topic yang di-crawl
    - Status ready/not-ready
    - Summary dari crawled content
    
    Fields:
    - id: UUID primary key
    - user_id: Foreign key ke User
    - title: Session title (user-friendly)
    - topic: Topic yang di-crawl (dari query)
    - vector_db_path: Path ke ChromaDB persistent storage
    - is_ready: Apakah crawler selesai dan siap untuk chat
    - summary: Optional summary dari crawled data
    - timestamps: created_at, updated_at
    """

    __tablename__ = "chat_sessions"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
        description="UUID primary key",
    )
    user_id: str = Field(
        foreign_key="users.id",
        max_length=36,
        index=True,
        description="Foreign key to User",
    )
    title: str = Field(
        max_length=255,
        description="Session title",
    )
    topic: str = Field(
        max_length=255,
        description="Topic yang di-crawl dari user query",
    )
    vector_db_path: str = Field(
        max_length=500,
        description="Path ke ChromaDB untuk session ini",
    )
    is_ready: bool = Field(
        default=False,
        description="Apakah crawl sudah selesai & data siap untuk chat",
    )
    summary: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Optional summary dari crawled content",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="Session creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        description="Last update timestamp",
    )


# ─── ChatMessage Model ────────────────────────────────────────────────────────


class ChatMessage(SQLModel, table=True):
    """
    Individual chat message model.
    
    Fields:
    - id: UUID primary key
    - session_id: Foreign key ke ChatSession
    - role: "user" atau "bot"
    - content: Message content (dapat text panjang)
    - order: Message ordering dalam session
    - created_at: Message creation timestamp
    """

    __tablename__ = "chat_messages"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
        description="UUID primary key",
    )
    session_id: str = Field(
        foreign_key="chat_sessions.id",
        max_length=36,
        index=True,
        description="Foreign key to ChatSession",
    )
    role: str = Field(
        max_length=20,
        description="'user' atau 'bot'",
    )
    content: str = Field(
        sa_column=Column(Text),
        description="Message content",
    )
    order: int = Field(
        default=0,
        description="Message order dalam session (untuk sorting)",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="Message creation timestamp",
    )

