"""
Alembic environment configuration for YDSF AI Article Chatbot.

Uses the project's existing engine (migration.base) and SQLModel metadata
so Alembic stays in sync with the SQLModel table definitions.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Ensure the project root is on sys.path ──────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Alembic Config object ──────────────────────────────────────────────────
config = context.config

# ── Logging ─────────────────────────────────────────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata (SQLModel / SQLAlchemy) ─────────────────────────────────
# Import models so all tables are registered on SQLModel.metadata
from migration import models  # noqa: F401
from sqlmodel import SQLModel

target_metadata = SQLModel.metadata

# ── Database URL from project config ────────────────────────────────────────
# Override the placeholder in alembic.ini with the real URL from .env
from migration.base import DATABASE_URL

config.set_main_option("sqlalchemy.url", DATABASE_URL)


# ── Offline mode ────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ─────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """Run migrations against a live database using the project engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


# ── Entry point ─────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
