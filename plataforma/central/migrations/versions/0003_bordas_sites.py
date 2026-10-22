"""Sites atendidos por cada nó de borda (autorização da borda e das credenciais locais).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE no_borda ADD COLUMN sites text[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE no_borda DROP COLUMN sites")
