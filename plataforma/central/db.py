"""
db.py — engine SQLAlchemy (Core) e definição das tabelas usadas pela central.

O DDL de verdade está em migrations/ (fonte da verdade do esquema); aqui só
declaramos as colunas que o código consulta, para montar SQL tipado.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
_JSON = JSON().with_variant(JSONB(), "postgresql")


def criar_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


inquilino = Table(
    "inquilino",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("slug", Text),
    Column("nome", Text),
    Column("cenario", Text),
    Column("criado_em", DateTime(timezone=True)),
)

site = Table(
    "site",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("inquilino_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("raio_km", Float),
)

area = Table(
    "area",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("site_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("raio_km", Float),
)

dominio = Table(
    "dominio",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("inquilino_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("descricao", Text),
)

perfil_dispositivo = Table(
    "perfil_dispositivo",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("dominio_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("intervalo_esperado_s", Integer),
)

perfil_grandeza = Table(
    "perfil_grandeza",
    metadata,
    Column("perfil_id", Uuid, primary_key=True),
    Column("grandeza", Text, primary_key=True),
    Column("unidade", Text),
    Column("valor_min", Float),
    Column("valor_max", Float),
)

no_borda = Table(
    "no_borda",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("inquilino_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("versao", Text),
    Column("fila_pendente", Integer),
    Column("ultimo_sync_em", DateTime(timezone=True)),
)

dispositivo = Table(
    "dispositivo",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("inquilino_id", Uuid),
    Column("perfil_id", Uuid),
    Column("site_id", Uuid),
    Column("area_id", Uuid),
    Column("slug", Text),
    Column("nome", Text),
    Column("tipo", Text),
    Column("fabricante", Text),
    Column("firmware", Text),
    Column("revogado_em", DateTime(timezone=True)),
    Column("ultimo_contato_em", DateTime(timezone=True)),
    Column("criado_em", DateTime(timezone=True)),
)

gemeo = Table(
    "gemeo",
    metadata,
    Column("dispositivo_id", Uuid, primary_key=True),
    Column("desejado", _JSON),
    Column("desejado_em", DateTime(timezone=True)),
    Column("relatado", _JSON),
    Column("relatado_em", DateTime(timezone=True)),
)

mensagem = Table(
    "mensagem",
    metadata,
    Column("id_mensagem", Uuid, primary_key=True),
    Column("dispositivo_id", Uuid),
    Column("tipo", Text),
    Column("medido_em", DateTime(timezone=True)),
    Column("recebido_em", DateTime(timezone=True)),
    Column("via", Text),
    Column("fonte", Text),
)

telemetria = Table(
    "telemetria",
    metadata,
    Column("tempo", DateTime(timezone=True)),
    Column("dispositivo_id", Uuid),
    Column("grandeza", Text),
    Column("valor", Float),
    Column("unidade", Text),
    Column("suspeita", Boolean),
    Column("id_mensagem", Uuid),
)

telemetria_1h = Table(
    "telemetria_1h",
    metadata,
    Column("hora", DateTime(timezone=True)),
    Column("dispositivo_id", Uuid),
    Column("grandeza", Text),
    Column("media", Float),
    Column("minimo", Float),
    Column("maximo", Float),
    Column("amostras", BigInteger),
)

ultimo_estado = Table(
    "ultimo_estado",
    metadata,
    Column("dispositivo_id", Uuid, primary_key=True),
    Column("grandeza", Text, primary_key=True),
    Column("valor", Float),
    Column("unidade", Text),
    Column("suspeita", Boolean),
    Column("tempo", DateTime(timezone=True)),
)

evento = Table(
    "evento",
    metadata,
    Column("tempo", DateTime(timezone=True)),
    Column("dispositivo_id", Uuid),
    Column("tipo", Text),
    Column("comando_id", Uuid),
    Column("id_mensagem", Uuid),
    Column("dados", _JSON),
)

auditoria = Table(
    "auditoria",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("quando", DateTime(timezone=True)),
    Column("inquilino_id", Uuid),
    Column("autor", Text),
    Column("entidade", Text),
    Column("entidade_id", Uuid),
    Column("acao", Text),
    Column("antes", _JSON),
    Column("depois", _JSON),
)

dlq = Table(
    "dlq",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("recebido_em", DateTime(timezone=True)),
    Column("origem", Text),
    Column("payload", Text),
    Column("motivo", Text),
)
