"""Regras, alarmes, comandos e assinaturas: campos do motor de regras (etapa A3).

- regra: nome único por inquilino;
- alarme: no máximo um alarme aberto por (dispositivo, regra) e uma entrega por
  assinante; quem resolveu, contagem de ocorrências, última atualização;
- comando: o id do comando é o id_mensagem da mensagem canônica enviada, e a
  resposta do dispositivo (evento com comando_id) fica registrada;
- execucao_acao: tipo e cópia da configuração da ação (também para entregas
  que não vêm de regra), erro e horário da última tentativa (retry com backoff);
- assinatura_alerta: nome legível.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE regra ADD CONSTRAINT regra_nome_unico UNIQUE (inquilino_id, nome);

    ALTER TABLE alarme
        ADD COLUMN resolvido_por  text,
        ADD COLUMN ocorrencias    integer NOT NULL DEFAULT 1,
        ADD COLUMN atualizado_em  timestamptz NOT NULL DEFAULT now();
    CREATE UNIQUE INDEX alarme_aberto_unico ON alarme (dispositivo_id, regra_id)
        WHERE estado <> 'resolvido' AND origem_alarme_id IS NULL;
    CREATE UNIQUE INDEX alarme_entrega_unica ON alarme (origem_alarme_id, inquilino_id)
        WHERE origem_alarme_id IS NOT NULL;
    CREATE INDEX ON alarme (origem_alarme_id);

    ALTER TABLE comando ADD COLUMN resposta jsonb;
    CREATE INDEX ON comando (dispositivo_id, enviado_em DESC);

    ALTER TABLE execucao_acao
        ALTER COLUMN regra_acao_id DROP NOT NULL,
        ADD COLUMN tipo          text NOT NULL DEFAULT 'alarme',
        ADD COLUMN config        jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN ultimo_erro   text,
        ADD COLUMN executado_em  timestamptz,
        ADD COLUMN criado_em     timestamptz NOT NULL DEFAULT now();
    CREATE INDEX ON execucao_acao (status, proxima_tentativa_em);

    ALTER TABLE assinatura_alerta ADD COLUMN nome text NOT NULL DEFAULT '';
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE assinatura_alerta DROP COLUMN nome;
    ALTER TABLE execucao_acao DROP COLUMN tipo, DROP COLUMN config, DROP COLUMN ultimo_erro,
        DROP COLUMN executado_em, DROP COLUMN criado_em;
    ALTER TABLE comando DROP COLUMN resposta;
    DROP INDEX IF EXISTS alarme_aberto_unico;
    DROP INDEX IF EXISTS alarme_entrega_unica;
    ALTER TABLE alarme DROP COLUMN resolvido_por, DROP COLUMN ocorrencias, DROP COLUMN atualizado_em;
    ALTER TABLE regra DROP CONSTRAINT regra_nome_unico;
    """)
