"""Alembic environment for WorkPilot's explicit database migrations."""

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from workpilot.config import Settings
from workpilot.persistence.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    settings_url = Settings().workpilot_database_url.get_secret_value()
    url = (
        os.environ.get("WORKPILOT_DATABASE_URL")
        or settings_url
        or config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError(
            "WORKPILOT_DATABASE_URL is required to run database migrations"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
