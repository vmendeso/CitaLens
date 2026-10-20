import os
from alembic import context
from sqlalchemy import create_engine, pool
from packages.common.models import Base

config = context.config
database_url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=Base.metadata,
                      literal_binds=True, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
