"""Tags e grupos de dispositivos (RF-DIS-07); grupos são tags no formato `grupo:xxx`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE dispositivo ADD COLUMN tags text[] NOT NULL DEFAULT '{}'")
    # GIN atende o filtro `tags @> ARRAY[...]` (todas as tags pedidas).
    op.execute("CREATE INDEX ix_dispositivo_tags ON dispositivo USING GIN (tags)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_dispositivo_tags")
    op.execute("ALTER TABLE dispositivo DROP COLUMN tags")
