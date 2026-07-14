"""Alembic 运行环境。"""

from logging.config import fileConfig
from os import environ

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def database_url() -> str:
    """读取 Owner DSN，并选择 SQLAlchemy 的 Psycopg 3 方言。"""

    configured = environ.get("ATLAS_DATABASE_URL")
    if not configured:
        raise RuntimeError("ATLAS_DATABASE_URL is required for database migrations")
    if configured.startswith("postgresql://"):
        return configured.replace("postgresql://", "postgresql+psycopg://", 1)
    return configured


def run_migrations_offline() -> None:
    """生成不连接数据库的 SQL。"""

    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在单个事务内执行 PostgreSQL DDL。"""

    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
