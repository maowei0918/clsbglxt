"""repair_quotes: 新增 note 列（报价备注）。

修复前：报价接口读取了 payload["note"]，但 RepairQuote 模型无该列，
note 被静默丢弃——前端传入的报价备注永远存不进库。

保留旧数据：NOT NULL 约束需用 server_default 兜底存量行。

Revision ID: 7c4e9b1d2a05
Revises: b3f81a2c6d47
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision: str = "7c4e9b1d2a05"
down_revision: str = "b3f81a2c6d47"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "repair_quotes" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("repair_quotes")}
    if "note" in cols:
        return  # 幂等：重复执行不会炸

    op.add_column(
        "repair_quotes",
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
    )
    # 去掉 server_default，保持与模型一致（默认由 Python 侧给出）。
    # SQLite 不支持 ALTER COLUMN DROP DEFAULT，其 server_default 保留
    # 也不影响写入（模型总会显式给 note）；只有 PG 需要清掉。
    if bind.dialect.name == "postgresql":
        op.execute(
            'ALTER TABLE "repair_quotes" ALTER COLUMN "note" DROP DEFAULT'
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "repair_quotes" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("repair_quotes")}
    if "note" not in cols:
        return

    op.drop_column("repair_quotes", "note")
