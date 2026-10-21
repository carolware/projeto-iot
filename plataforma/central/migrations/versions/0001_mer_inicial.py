"""MER inicial: catálogo, dispositivos, séries temporais (TimescaleDB), regras, alarmes, auditoria.

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SLUG = r"CHECK ({col} ~ '^[a-z0-9][a-z0-9_-]*$')"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # ── Catálogo e locais (RF-CEN-01..03) ────────────────────────────────────
    op.execute(f"""
    CREATE TABLE inquilino (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug        text NOT NULL UNIQUE {SLUG.format(col="slug")},
        nome        text NOT NULL,
        cenario     text NOT NULL CHECK (cenario IN ('urbano', 'rural')),
        criado_em   timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE site (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id  uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        slug          text NOT NULL {SLUG.format(col="slug")},
        nome          text NOT NULL,
        lat           double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
        lon           double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
        raio_km       double precision CHECK (raio_km > 0),
        UNIQUE (inquilino_id, slug)
    );

    CREATE TABLE area (
        id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        site_id   uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        slug      text NOT NULL {SLUG.format(col="slug")},
        nome      text NOT NULL,
        lat       double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
        lon       double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
        raio_km   double precision CHECK (raio_km > 0),
        UNIQUE (site_id, slug)
    );

    CREATE TABLE dominio (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id  uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        slug          text NOT NULL {SLUG.format(col="slug")},
        nome          text NOT NULL,
        descricao     text,
        UNIQUE (inquilino_id, slug)
    );

    CREATE TABLE perfil_dispositivo (
        id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        dominio_id            uuid NOT NULL REFERENCES dominio(id) ON DELETE CASCADE,
        slug                  text NOT NULL {SLUG.format(col="slug")},
        nome                  text NOT NULL,
        intervalo_esperado_s  integer NOT NULL CHECK (intervalo_esperado_s > 0),
        UNIQUE (dominio_id, slug)
    );

    CREATE TABLE perfil_grandeza (
        perfil_id  uuid NOT NULL REFERENCES perfil_dispositivo(id) ON DELETE CASCADE,
        grandeza   text NOT NULL {SLUG.format(col="grandeza")},
        unidade    text NOT NULL,
        valor_min  double precision,
        valor_max  double precision,
        PRIMARY KEY (perfil_id, grandeza),
        CHECK (valor_min IS NULL OR valor_max IS NULL OR valor_min <= valor_max)
    );

    CREATE TABLE contato_resgate (
        id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        site_id   uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        nome      text NOT NULL,
        telefone  text,
        email     text
    );
    """)

    # ── Dispositivos e borda (RF-DIS, RF-EDG) ────────────────────────────────
    op.execute(f"""
    CREATE TABLE no_borda (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id     uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        slug             text NOT NULL UNIQUE {SLUG.format(col="slug")},
        nome             text NOT NULL,
        versao           text,
        credencial_hash  text,
        fila_pendente    integer NOT NULL DEFAULT 0,
        ultimo_sync_em   timestamptz
    );

    CREATE TABLE dispositivo (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id       uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        perfil_id          uuid NOT NULL REFERENCES perfil_dispositivo(id),
        site_id            uuid NOT NULL REFERENCES site(id),
        area_id            uuid REFERENCES area(id),
        slug               text NOT NULL UNIQUE {SLUG.format(col="slug")},
        nome               text NOT NULL,
        tipo               text NOT NULL CHECK (tipo IN ('estacao', 'terminal', 'atuador', 'conector')),
        fabricante         text,
        firmware           text,
        credencial_hash    text,
        revogado_em        timestamptz,
        ultimo_contato_em  timestamptz,
        criado_em          timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ON dispositivo (inquilino_id);

    CREATE TABLE gemeo (
        dispositivo_id  uuid PRIMARY KEY REFERENCES dispositivo(id) ON DELETE CASCADE,
        desejado        jsonb NOT NULL DEFAULT '{{}}',
        desejado_em     timestamptz,
        relatado        jsonb NOT NULL DEFAULT '{{}}',
        relatado_em     timestamptz
    );
    """)

    # ── Mensagens e séries temporais (RF-ING-04/08, RF-DAD-01/02) ────────────
    op.execute("""
    CREATE TABLE mensagem (
        id_mensagem     uuid PRIMARY KEY,
        dispositivo_id  uuid NOT NULL REFERENCES dispositivo(id) ON DELETE CASCADE,
        tipo            text NOT NULL CHECK (tipo IN ('telemetria', 'evento', 'estado')),
        medido_em       timestamptz NOT NULL,
        recebido_em     timestamptz NOT NULL,
        via             text,
        fonte           text
    );
    CREATE INDEX ON mensagem (dispositivo_id, medido_em DESC);

    CREATE TABLE telemetria (
        tempo           timestamptz NOT NULL,
        dispositivo_id  uuid NOT NULL,
        grandeza        text NOT NULL,
        valor           double precision NOT NULL,
        unidade         text NOT NULL,
        suspeita        boolean NOT NULL DEFAULT false,
        id_mensagem     uuid NOT NULL
    );
    SELECT create_hypertable('telemetria', by_range('tempo', INTERVAL '1 day'));
    CREATE INDEX ON telemetria (dispositivo_id, grandeza, tempo DESC);

    CREATE TABLE ultimo_estado (
        dispositivo_id  uuid NOT NULL REFERENCES dispositivo(id) ON DELETE CASCADE,
        grandeza        text NOT NULL,
        valor           double precision NOT NULL,
        unidade         text NOT NULL,
        suspeita        boolean NOT NULL DEFAULT false,
        tempo           timestamptz NOT NULL,
        PRIMARY KEY (dispositivo_id, grandeza)
    );

    CREATE TABLE evento (
        tempo           timestamptz NOT NULL,
        dispositivo_id  uuid NOT NULL,
        tipo            text NOT NULL,
        comando_id      uuid,
        id_mensagem     uuid,
        dados           jsonb NOT NULL DEFAULT '{}'
    );
    SELECT create_hypertable('evento', by_range('tempo', INTERVAL '7 days'));
    CREATE INDEX ON evento (dispositivo_id, tempo DESC);

    ALTER TABLE telemetria SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'dispositivo_id, grandeza'
    );
    SELECT add_compression_policy('telemetria', INTERVAL '7 days');
    """)

    # ── Fonte orbital (conector FIRMS) ───────────────────────────────────────
    op.execute("""
    CREATE TABLE varredura_firms (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        site_id       uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        executada_em  timestamptz NOT NULL,
        raio_km       double precision NOT NULL,
        janela_horas  integer NOT NULL,
        focos         integer NOT NULL DEFAULT 0,
        frp_max       double precision NOT NULL DEFAULT 0,
        distancia_km  double precision,
        erro          text
    );
    CREATE INDEX ON varredura_firms (site_id, executada_em DESC);

    CREATE TABLE foco_firms (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        varredura_id   uuid NOT NULL REFERENCES varredura_firms(id) ON DELETE CASCADE,
        latitude       double precision NOT NULL,
        longitude      double precision NOT NULL,
        frp            double precision NOT NULL DEFAULT 0,
        confidence     text,
        satellite      text,
        instrument     text,
        adquirido_em   timestamptz,
        daynight       text,
        distancia_km   double precision
    );
    """)

    # ── Regras, alarmes, assinaturas e comandos (RF-PRO, RF-DIS-05) ──────────
    op.execute("""
    CREATE TABLE regra (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id  uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        dominio_id    uuid NOT NULL REFERENCES dominio(id) ON DELETE CASCADE,
        nome          text NOT NULL,
        condicao      jsonb NOT NULL,
        janela_s      integer NOT NULL DEFAULT 0 CHECK (janela_s >= 0),
        severidade    text NOT NULL CHECK (severidade IN ('medio', 'alto', 'critico')),
        escopo        text NOT NULL DEFAULT 'central' CHECK (escopo IN ('central', 'borda')),
        ativa         boolean NOT NULL DEFAULT true
    );

    CREATE TABLE regra_acao (
        id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        regra_id  uuid NOT NULL REFERENCES regra(id) ON DELETE CASCADE,
        tipo      text NOT NULL CHECK (tipo IN ('alarme', 'comando', 'webhook', 'publicar', 'notificar')),
        config    jsonb NOT NULL DEFAULT '{}',
        ordem     integer NOT NULL DEFAULT 0
    );

    CREATE TABLE assinatura_alerta (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id    uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        dominio_slug    text NOT NULL,
        lat             double precision NOT NULL,
        lon             double precision NOT NULL,
        raio_km         double precision NOT NULL CHECK (raio_km > 0),
        severidade_min  text NOT NULL DEFAULT 'alto' CHECK (severidade_min IN ('medio', 'alto', 'critico')),
        ativa           boolean NOT NULL DEFAULT true
    );

    CREATE TABLE alarme (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        inquilino_id      uuid NOT NULL REFERENCES inquilino(id) ON DELETE CASCADE,
        regra_id          uuid REFERENCES regra(id) ON DELETE SET NULL,
        dispositivo_id    uuid REFERENCES dispositivo(id) ON DELETE SET NULL,
        assinatura_id     uuid REFERENCES assinatura_alerta(id) ON DELETE SET NULL,
        origem_alarme_id  uuid REFERENCES alarme(id) ON DELETE SET NULL,
        severidade        text NOT NULL CHECK (severidade IN ('medio', 'alto', 'critico')),
        estado            text NOT NULL DEFAULT 'ativo' CHECK (estado IN ('ativo', 'reconhecido', 'resolvido')),
        ocorrido_em       timestamptz NOT NULL,
        reconhecido_em    timestamptz,
        reconhecido_por   text,
        resolvido_em      timestamptz,
        dados             jsonb NOT NULL DEFAULT '{}'
    );
    CREATE INDEX ON alarme (inquilino_id, estado, ocorrido_em DESC);

    CREATE TABLE comando (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        dispositivo_id  uuid NOT NULL REFERENCES dispositivo(id) ON DELETE CASCADE,
        alarme_id       uuid REFERENCES alarme(id) ON DELETE SET NULL,
        acao            text NOT NULL,
        payload         jsonb NOT NULL DEFAULT '{}',
        origem          text NOT NULL CHECK (origem IN ('regra', 'painel', 'borda')),
        estado          text NOT NULL DEFAULT 'pendente'
                        CHECK (estado IN ('pendente', 'enviado', 'confirmado', 'falhou')),
        enviado_em      timestamptz,
        respondido_em   timestamptz
    );

    CREATE TABLE execucao_acao (
        id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        alarme_id             uuid NOT NULL REFERENCES alarme(id) ON DELETE CASCADE,
        regra_acao_id         uuid NOT NULL REFERENCES regra_acao(id) ON DELETE CASCADE,
        tentativas            integer NOT NULL DEFAULT 0,
        status                text NOT NULL DEFAULT 'pendente',
        proxima_tentativa_em  timestamptz
    );
    """)

    # ── Auditoria e mensagens inválidas (RF-DAD-06, RF-ING-06) ───────────────
    op.execute("""
    CREATE TABLE auditoria (
        id            bigserial PRIMARY KEY,
        quando        timestamptz NOT NULL DEFAULT now(),
        inquilino_id  uuid REFERENCES inquilino(id) ON DELETE CASCADE,
        autor         text NOT NULL,
        entidade      text NOT NULL,
        entidade_id   uuid,
        acao          text NOT NULL,
        antes         jsonb,
        depois        jsonb
    );
    CREATE INDEX ON auditoria (inquilino_id, quando DESC);

    CREATE TABLE dlq (
        id           bigserial PRIMARY KEY,
        recebido_em  timestamptz NOT NULL DEFAULT now(),
        origem       text NOT NULL,
        payload      text NOT NULL,
        motivo       text NOT NULL
    );
    CREATE INDEX ON dlq (recebido_em DESC);
    """)

    # ── Agregação horária contínua (RF-PRO-04) ───────────────────────────────
    # CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous) não roda dentro de transação.
    with op.get_context().autocommit_block():
        op.execute("""
        CREATE MATERIALIZED VIEW telemetria_1h
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket(INTERVAL '1 hour', tempo) AS hora,
               dispositivo_id,
               grandeza,
               avg(valor)  AS media,
               min(valor)  AS minimo,
               max(valor)  AS maximo,
               count(*)    AS amostras
          FROM telemetria
         GROUP BY hora, dispositivo_id, grandeza
          WITH NO DATA
        """)
        # janela de 3 dias cobre dados atrasados vindos da borda (RNF-04: 72 h)
        op.execute("""
        SELECT add_continuous_aggregate_policy('telemetria_1h',
            start_offset => INTERVAL '3 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '30 minutes')
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetria_1h")
    op.execute("""
    DROP TABLE IF EXISTS dlq, auditoria, execucao_acao, comando, alarme, assinatura_alerta,
        regra_acao, regra, foco_firms, varredura_firms, evento, ultimo_estado, telemetria,
        mensagem, gemeo, dispositivo, no_borda, contato_resgate, perfil_grandeza,
        perfil_dispositivo, dominio, area, site, inquilino CASCADE
    """)
