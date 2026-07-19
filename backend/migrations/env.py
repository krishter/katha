import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load DATABASE_URL from env, falling back to the default for local dev.
# asyncpg is async-only; swap to psycopg2/pg8000 URL scheme for Alembic's
# sync engine by replacing the driver prefix.
_db_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://katha:katha@localhost:5432/katha",
).replace("postgresql+asyncpg://", "postgresql://")

config.set_main_option("sqlalchemy.url", _db_url)

# Import models so Alembic can detect schema changes for autogenerate
import models.consent_record  # noqa: E402, F401
import models.fact  # noqa: E402, F401
import models.family_account  # noqa: E402, F401
import models.magic_link_token  # noqa: E402, F401
import models.memory_card  # noqa: E402, F401
import models.session  # noqa: E402, F401
import models.story_atom  # noqa: E402, F401
import models.user_profile  # noqa: E402, F401
from models.db import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
