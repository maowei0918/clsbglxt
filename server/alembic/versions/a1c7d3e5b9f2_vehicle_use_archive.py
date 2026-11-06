"""临时用车归档字段

给 temp_vehicle_uses 增加 archived / archived_at：
  * archived     是否已归档（默认 false，存量记录视为未归档）
  * archived_at  归档时间（未归档为 NULL）

用 server_default 回填存量行，避免 NOT NULL 约束在已有数据上失败。

Revision ID: a1c7d3e5b9f2
Revises: 0f621c8e0828
Create Date: 2026-11-05 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c7d3e5b9f2'
down_revision: Union[str, None] = '0f621c8e0828'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 让 PostgreSQL 一次性回填存量行（NOT NULL 才不会报错）
    op.add_column(
        'temp_vehicle_uses',
        sa.Column('archived', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        'temp_vehicle_uses',
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f('ix_temp_vehicle_uses_archived'),
        'temp_vehicle_uses', ['archived'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_temp_vehicle_uses_archived'),
                  table_name='temp_vehicle_uses')
    op.drop_column('temp_vehicle_uses', 'archived_at')
    op.drop_column('temp_vehicle_uses', 'archived')