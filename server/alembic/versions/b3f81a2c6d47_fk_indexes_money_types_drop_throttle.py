"""补外键索引 + 金额列统一 numeric(12,2) + 删除 login_throttle 死表

三类修复合并在一条迁移里，都属于「模型声明与实际 schema 漂移」：

1. 外键索引（31 个）
   模型里大量 FK 列没有索引，部门隔离（users.department_id）、
   可见性判定（repair_orders.reporter_id）、子表按主表查询
   （repair_actions.order_id 等）都会全表扫。数据量上来即慢查询。
   用 CREATE INDEX IF NOT EXISTS + CONCURRENTLY 语义不适用（alembic
   事务内），这里用普通 create_index，索引体量小、建锁时间可接受。

2. 金额列类型
   models.py 声明 Numeric(12,2)（精确小数），但基线迁移
   0f621c8e0828_initial.py 建的是 sa.Float()。走迁移建库的站点金额
   实际是 double precision → 浮点误差、对账漂移。
   这里统一 ALTER 为 numeric(12,2)。USING 显式转换，避免
   float→numeric 的隐式舍入差异报错。

3. login_throttle 表
   限频早已改走 Redis 滑动窗口，该表无任何读写（grep 仅命中模型
   与基线迁移）。死表占迁移体积、误导维护者，删除。

Revision ID: b3f81a2c6d47
Revises: a1c7d3e5b9f2
Create Date: 2026-11-07 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f81a2c6d47'
down_revision: Union[str, None] = 'a1c7d3e5b9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表名, 索引名, 列名) —— 覆盖审计确认的 31 个未索引外键
FK_INDEXES = [
    ('vehicles', 'ix_vehicles_vehicle_type_id', 'vehicle_type_id'),
    ('users', 'ix_users_department_id', 'department_id'),
    ('users', 'ix_users_supplier_id', 'supplier_id'),
    ('role_permissions', 'ix_role_permissions_role_id', 'role_id'),
    ('contract_vehicle_types', 'ix_cvt_contract_id', 'contract_id'),
    ('contract_vehicle_types', 'ix_cvt_vehicle_type_id', 'vehicle_type_id'),
    ('contract_items', 'ix_contract_items_contract_id', 'contract_id'),
    ('contract_items', 'ix_contract_items_vehicle_type_id', 'vehicle_type_id'),
    ('backup_jobs', 'ix_backup_jobs_target_id', 'target_id'),
    ('user_roles', 'ix_user_roles_role_id', 'role_id'),
    ('user_roles', 'ix_user_roles_user_id', 'user_id'),
    ('repair_orders', 'ix_repair_orders_quote_owner_id', 'quote_owner_id'),
    ('repair_orders', 'ix_repair_orders_target_supplier_id', 'target_supplier_id'),
    ('repair_orders', 'ix_repair_orders_template_id', 'template_id'),
    ('repair_orders', 'ix_repair_orders_reporter_id', 'reporter_id'),
    ('daily_check_records', 'ix_dcr_user_id', 'user_id'),
    ('temp_vehicle_uses', 'ix_tvu_created_by', 'created_by'),
    ('temp_vehicle_uses', 'ix_tvu_vehicle_id', 'vehicle_id'),
    ('temp_vehicle_uses', 'ix_tvu_vehicle_type_id', 'vehicle_type_id'),
    ('api_keys', 'ix_api_keys_created_by', 'created_by'),
    ('repair_actions', 'ix_repair_actions_order_id', 'order_id'),
    ('repair_actions', 'ix_repair_actions_user_id', 'user_id'),
    ('repair_quotes', 'ix_repair_quotes_contract_id', 'contract_id'),
    ('repair_quotes', 'ix_repair_quotes_order_id', 'order_id'),
    ('repair_quotes', 'ix_repair_quotes_user_id', 'user_id'),
    ('repair_cc', 'ix_repair_cc_order_id', 'order_id'),
    ('repair_cc', 'ix_repair_cc_user_id', 'user_id'),
    ('daily_check_photos', 'ix_dcp_record_id', 'record_id'),
    ('daily_check_photos', 'ix_dcp_user_id', 'user_id'),
    ('repair_quote_items', 'ix_rqi_contract_item_id', 'contract_item_id'),
    ('repair_quote_items', 'ix_rqi_quote_id', 'quote_id'),
    ('attachments', 'ix_attachments_action_id', 'action_id'),
]

# (表名, 列名) —— 模型声明 Numeric(12,2) 但基线迁移建成 Float 的列
MONEY_COLUMNS = [
    ('repair_orders', 'part_price'),
    ('repair_orders', 'labor_price'),
    ('repair_orders', 'total_part'),
    ('repair_orders', 'total_labor'),
    ('repair_orders', 'total'),
    ('repair_orders', 'qty'),
    ('repair_quote_items', 'part_price'),
    ('repair_quote_items', 'labor_price'),
    ('repair_quote_items', 'contract_part_price'),
    ('repair_quote_items', 'contract_labor_price'),
    ('che1_fuel_records', 'amount'),
]


def _is_pg() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # 1) 外键索引（跳过不存在的表，便于跨版本复用）
    for table, index_name, column in FK_INDEXES:
        if table not in existing_tables:
            continue
        cols = {c['name'] for c in insp.get_columns(table)}
        if column not in cols:
            continue
        idx_names = {i['name'] for i in insp.get_indexes(table)}
        if index_name in idx_names:
            continue
        op.create_index(index_name, table, [column], unique=False)

    # 2) 金额列统一为 numeric(12,2)
    if _is_pg():
        # PostgreSQL：ALTER TYPE + USING 显式转换
        for table, column in MONEY_COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c['name']: c for c in insp.get_columns(table)}
            if column not in cols:
                continue
            current = cols[column]['type']
            if isinstance(current, sa.Numeric):
                continue
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE numeric(12,2) USING "{column}"::numeric(12,2)'
            )
    # SQLite 无 ALTER COLUMN TYPE，金额列在本地开发库保持现状
    # （SQLite 是动态类型，精度问题只在 PG 生产库有意义）

    # 3) 删除已废弃的 login_throttle 表
    if 'login_throttle' in existing_tables:
        op.drop_table('login_throttle')


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    # 3) 恢复 login_throttle（结构取自基线迁移）
    if 'login_throttle' not in existing_tables:
        op.create_table(
            'login_throttle',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('key', sa.String(length=128), nullable=False),
            sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_login_throttle_key', 'login_throttle', ['key'])
        op.create_index('ix_login_throttle_ts', 'login_throttle', ['ts'])

    # 2) 金额列回退为 Float（仅 PG）
    if _is_pg():
        for table, column in MONEY_COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c['name'] for c in insp.get_columns(table)}
            if column not in cols:
                continue
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE double precision USING "{column}"::double precision'
            )

    # 1) 删除本次新建的索引
    for table, index_name, _column in FK_INDEXES:
        if table not in existing_tables:
            continue
        idx_names = {i['name'] for i in insp.get_indexes(table)}
        if index_name in idx_names:
            op.drop_index(index_name, table_name=table)