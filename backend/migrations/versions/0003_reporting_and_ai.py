"""reporting layer: materialized view, definer refresh function and the `ai` schema views (07 sections 1.2 and 1.4)

Revision ID: 0003
Revises: 0002

The DDL lives in `app.db.contract_sql`, the single source that `export-contract` also writes to `contract/ai_views.sql`.
"""

from collections.abc import Sequence

from alembic import op

from app.db.contract_sql import (
    AI_VIEWS_SQL,
    DROP_AI_VIEWS_SQL,
    DROP_REPORTING_SQL,
    REPORTING_SQL,
    statements,
)

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in (*statements(REPORTING_SQL), *statements(AI_VIEWS_SQL)):
        op.execute(statement)


def downgrade() -> None:
    for statement in (*statements(DROP_AI_VIEWS_SQL), *statements(DROP_REPORTING_SQL)):
        op.execute(statement)
