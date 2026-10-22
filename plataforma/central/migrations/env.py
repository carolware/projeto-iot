from alembic import context
from sqlalchemy import create_engine

from central.config import configuracao

url = context.config.attributes.get("url") or configuracao().database_url


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as conexao:
        context.configure(connection=conexao)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
