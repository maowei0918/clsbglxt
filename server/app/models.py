"""clglxt 数据库模型（PostgreSQL 版）。

基于 FMS v2 模型定义，适配 PostgreSQL：
  * 连接池 + pre_ping
  * 统一 DateTime(timezone=True)，存 UTC
  * 软删除保留 EPOCH 哨兵，保持数据语义与 FMS 一致，便于 API 导入
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, Numeric, String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.database import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


# 软删除哨兵（与 FMS 一致）
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
EPOCH_PREFIX = "1970-01-01"


def alive(column):
    """「该行未被软删」的过滤条件：deleted_at == EPOCH 哨兵。

    注意：原实现用 column.like("1970-01-01%")，DateTime 列在 PostgreSQL
    上不支持 LIKE 操作符（operator does not exist: timestamp ~~ unknown），
    且 SQLite 下隐式转换也不可靠。统一改为等值比较，PG/SQLite 均可用。
    deleted_at 为 nullable=False、默认 EPOCH，故不可用 is_(None) 判定。
    """
    return column == EPOCH


def _epoch() -> datetime:
    def _val():
        return EPOCH
    return EPOCH


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    remark: Mapped[str] = mapped_column(String(255), default="")


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    contact: Mapped[str] = mapped_column(String(32), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    remark: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 可见范围
    serves_all_depts: Mapped[bool] = mapped_column(Boolean, default=False)
    dept_ids: Mapped[str] = mapped_column(Text, default="[]")
    see_all_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    see_contract_price: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_templates: Mapped[bool] = mapped_column(Boolean, default=False)
    perm_extra: Mapped[str] = mapped_column(Text, default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VehicleType(Base):
    __tablename__ = "vehicle_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    remark: Mapped[str] = mapped_column(String(255), default="")


class VehicleNature(Base):
    __tablename__ = "vehicle_natures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    remark: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate_no: Mapped[str] = mapped_column(String(16), unique=True)
    vehicle_type_id: Mapped[int] = mapped_column(ForeignKey("vehicle_types.id"))
    nature: Mapped[str] = mapped_column(String(32), default="")
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    brand: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="在用")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    vehicle_type = relationship("VehicleType")
    department = relationship("Department")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(32))
    phone: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_epoch, nullable=False)
    deleted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    department = relationship("Department")
    supplier = relationship("Supplier")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    remark: Mapped[str] = mapped_column(String(255), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    side: Mapped[str] = mapped_column(String(16), default="internal")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    perm_code: Mapped[str] = mapped_column(String(64))


class UserRole(Base):
    __tablename__ = "user_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    supplier: Mapped[str] = mapped_column(String(128), default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    natures: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    vehicle_types = relationship("VehicleType", secondary="contract_vehicle_types")
    items = relationship("ContractItem", back_populates="contract")


class ContractVehicleType(Base):
    __tablename__ = "contract_vehicle_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    vehicle_type_id: Mapped[int] = mapped_column(ForeignKey("vehicle_types.id"))


class ContractItem(Base):
    __tablename__ = "contract_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    vehicle_type_id: Mapped[int] = mapped_column(ForeignKey("vehicle_types.id"), nullable=False)
    part_name: Mapped[str] = mapped_column(String(128))
    spec: Mapped[str] = mapped_column(String(64), default="")
    unit: Mapped[str] = mapped_column(String(16), default="个")
    part_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    labor_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    remark: Mapped[str] = mapped_column(String(255), default="")

    contract = relationship("Contract", back_populates="items")
    vehicle_type = relationship("VehicleType")


class FlowTemplate(Base):
    __tablename__ = "flow_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    steps: Mapped[str] = mapped_column(Text)


class RepairOrder(Base):
    __tablename__ = "repair_orders"
    __table_args__ = (UniqueConstraint("reporter_id", "idem_key",
                                       name="ux_repair_orders_idem"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    template_snapshot: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("flow_templates.id"), nullable=True)
    current_seq: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="processing", index=True)
    target_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    quote_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repair_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    repair_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    idem_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    quote_owner = relationship("User", foreign_keys=[quote_owner_id])
    target_supplier = relationship("Supplier", foreign_keys=[target_supplier_id])


class RepairAction(Base):
    __tablename__ = "repair_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user = relationship("User")


class RepairQuote(Base):
    __tablename__ = "repair_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    total_part: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    total_labor: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    has_diff: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    items = relationship("RepairQuoteItem", back_populates="quote")
    user = relationship("User")


class RepairQuoteItem(Base):
    __tablename__ = "repair_quote_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("repair_quotes.id"))
    part_name: Mapped[str] = mapped_column(String(128))
    spec: Mapped[str] = mapped_column(String(64), default="")
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('1'))
    part_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    labor_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'))
    contract_item_id: Mapped[int | None] = mapped_column(ForeignKey("contract_items.id"), nullable=True)
    contract_part_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    contract_labor_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_diff: Mapped[bool] = mapped_column(Boolean, default=False)
    diff_note: Mapped[str] = mapped_column(String(255), default="")

    quote = relationship("RepairQuote", back_populates="items")


class RepairCC(Base):
    __tablename__ = "repair_cc"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    step_seq: Mapped[int] = mapped_column(Integer, default=1)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), nullable=True, index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("repair_actions.id"), nullable=True)
    step_seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(8))
    saved_path: Mapped[str] = mapped_column(String(255))
    orig_name: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    from_camera: Mapped[bool] = mapped_column(Boolean, default=True)


class DailyCheckTemplate(Base):
    __tablename__ = "daily_check_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    items: Mapped[str] = mapped_column(Text, default="[]")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    min_photos: Mapped[int] = mapped_column(Integer, default=0)
    max_photos: Mapped[int] = mapped_column(Integer, default=3)
    ai_review: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_prompt: Mapped[str] = mapped_column(Text, default="")
    ai_fail_mode: Mapped[str] = mapped_column(String(16), default="remind")


class DailyCheckRecord(Base):
    __tablename__ = "daily_check_records"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "check_date", "deleted_at",
                         name="uq_vehicle_date_alive"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    check_date: Mapped[date] = mapped_column(Date, index=True)
    results: Mapped[str] = mapped_column(Text)
    has_abnormal: Mapped[bool] = mapped_column(Boolean, default=False)
    photos: Mapped[str] = mapped_column(Text, default="[]")
    ai: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_epoch, nullable=False)
    deleted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_reason: Mapped[str] = mapped_column(String(200), default="")

    vehicle = relationship("Vehicle")
    user = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    # SQLite 只对 INTEGER PRIMARY KEY 自动自增；PG 用 BigInteger
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    username: Mapped[str] = mapped_column(String(32), default="")
    user_name: Mapped[str] = mapped_column(String(32), default="")

    ip: Mapped[str] = mapped_column(String(64), default="")
    client: Mapped[str] = mapped_column(String(64), default="")
    os: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    browser: Mapped[str] = mapped_column(String(64), default="")
    ua_hash: Mapped[str] = mapped_column(String(32), default="")
    device_name: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str] = mapped_column(String(32), default="")
    ua: Mapped[str] = mapped_column(String(300), default="")


class DailyCheckPhoto(Base):
    __tablename__ = "daily_check_photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("daily_check_records.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    saved_path: Mapped[str] = mapped_column(String(255))
    orig_name: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AiUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    usage_date: Mapped[str] = mapped_column(String(10))
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ── 备份 ──

class BackupTarget(Base):
    __tablename__ = "backup_targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="local")
    base_path: Mapped[str] = mapped_column(String(512), default="")
    s3_endpoint: Mapped[str] = mapped_column(String(255), default="")
    s3_bucket: Mapped[str] = mapped_column(String(128), default="")
    s3_region: Mapped[str] = mapped_column(String(32), default="us-east-1")
    s3_access_key: Mapped[str] = mapped_column(String(128), default="")
    s3_secret: Mapped[str] = mapped_column(String(255), default="")
    s3_prefix: Mapped[str] = mapped_column(String(255), default="fms")
    dav_url: Mapped[str] = mapped_column(String(255), default="")
    dav_secure: Mapped[bool] = mapped_column(Boolean, default=True)
    dav_host: Mapped[str] = mapped_column(String(255), default="")
    dav_port: Mapped[int] = mapped_column(Integer, default=0)
    dav_path: Mapped[str] = mapped_column(String(255), default="")
    dav_user: Mapped[str] = mapped_column(String(64), default="")
    dav_pass: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BackupJob(Base):
    __tablename__ = "backup_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[int] = mapped_column(ForeignKey("backup_targets.id"))
    mode: Mapped[str] = mapped_column(String(16), default="incremental")
    schedule_type: Mapped[str] = mapped_column(String(16), default="manual")
    schedule_hour: Mapped[int] = mapped_column(Integer, default=3)
    schedule_minute: Mapped[int] = mapped_column(Integer, default=0)
    schedule_weekday: Mapped[int] = mapped_column(Integer, default=0)
    schedule_monthday: Mapped[int] = mapped_column(Integer, default=1)
    keep_count: Mapped[int] = mapped_column(Integer, default=7)
    preserve_mtime: Mapped[bool] = mapped_column(Boolean, default=True)
    rewrite_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    rewrite_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    target = relationship("BackupTarget")


class BackupRun(Base):
    __tablename__ = "backup_runs"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer)
    job_name: Mapped[str] = mapped_column(String(64), default="")
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    mode: Mapped[str] = mapped_column(String(16), default="incremental")
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_done: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0)
    snapshot_dir: Mapped[str] = mapped_column(String(512), default="")
    message: Mapped[str] = mapped_column(String(1000), default="")


class BackupItem(Base):
    __tablename__ = "backup_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer)
    rel_path: Mapped[str] = mapped_column(String(512), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str] = mapped_column(String(500), default="")


class BackupRestoreRun(Base):
    __tablename__ = "backup_restore_runs"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(Integer)
    root: Mapped[str] = mapped_column(String(512), default="")
    mode: Mapped[str] = mapped_column(String(16), default="fill")
    status: Mapped[str] = mapped_column(String(16), default="running")
    operator_id: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied: Mapped[str] = mapped_column(Text, default="")
    attachments_done: Mapped[int] = mapped_column(Integer, default=0)
    attachments_failed: Mapped[int] = mapped_column(Integer, default=0)
    safety_db: Mapped[str] = mapped_column(String(512), default="")
    message: Mapped[str] = mapped_column(String(1000), default="")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttle"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    ts: Mapped[float] = mapped_column(Float, default=0.0, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[int] = mapped_column(Integer, default=0)
    actor_id: Mapped[int] = mapped_column(Integer, default=0)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class SeqCounter(Base):
    __tablename__ = "seq_counters"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class AppRelease(Base):
    __tablename__ = "app_releases"
    __table_args__ = (UniqueConstraint("platform", "version_code", name="uq_app_platform_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    version_name: Mapped[str] = mapped_column(String(32))
    version_code: Mapped[int] = mapped_column(Integer)
    min_code: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(255))
    file_md5: Mapped[str] = mapped_column(String(32), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(8), default="builtin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TempVehicleUse(Base):
    __tablename__ = "temp_vehicle_uses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate_no: Mapped[str] = mapped_column(String(16), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    vehicle_type_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_types.id"), nullable=True)
    nature: Mapped[str] = mapped_column(String(32), default="")
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(255), default="")
    park_location: Mapped[str] = mapped_column(String(255), default="")
    remark: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vehicle_type = relationship("VehicleType")
    department = relationship("Department")


# ── 夜间围栏 ──

class FencePoint(Base):
    __tablename__ = "fence_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    plate_no: Mapped[str] = mapped_column(String(16), index=True)
    input_text: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_m: Mapped[int] = mapped_column(Integer, default=0)
    resolved_by: Mapped[str] = mapped_column(String(16), default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[int] = mapped_column(Integer, default=0)


class FenceAlert(Base):
    __tablename__ = "fence_alerts"
    __table_args__ = (UniqueConstraint("plate_no", "night_date", "kind",
                                       name="uq_fence_alert_plate_night_kind"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate_no: Mapped[str] = mapped_column(String(16), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    night_date: Mapped[str] = mapped_column(String(10), default="")
    kind: Mapped[str] = mapped_column(String(24), default="away")
    expect_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    expect_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    expect_addr: Mapped[str] = mapped_column(String(300), default="")
    actual_addr: Mapped[str] = mapped_column(String(300), default="")
    pos_time: Mapped[str] = mapped_column(String(24), default="")
    source: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class FenceNightRun(Base):
    __tablename__ = "fence_night_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    night_date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    checked: Mapped[int] = mapped_column(Integer, default=0)
    alerts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(24), default="ok")
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class GpsCache(Base):
    __tablename__ = "gps_geocode_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidates: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ── che1 车辆台账同步（行驶里程 / 加油记录） ──

class Che1Vehicle(Base):
    """che1 车辆台账快照（按车牌匹配 FMS 车辆）。"""
    __tablename__ = "che1_vehicles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_id: Mapped[int] = mapped_column(Integer, unique=True, comment="che1 vehicleId")
    plate_no: Mapped[str] = mapped_column(String(32), index=True, comment="che1 原样车牌")
    company_name: Mapped[str] = mapped_column(String(64), default="", comment="che1 所属单位")
    fms_vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id"), nullable=True, index=True, comment="匹配到的 FMS 车辆")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = {"comment": "che1 车辆台账快照"}


class Che1Usage(Base):
    """che1 派车记录（出/回车时间 + 里程表读数）。"""
    __tablename__ = "che1_usage_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_id: Mapped[str] = mapped_column(String(64), unique=True, comment="che1 记录 id")
    che1_vehicle_id: Mapped[int] = mapped_column(ForeignKey("che1_vehicles.id"), index=True)
    # 冗余 FMS 车辆 id 供报表直查；不加外键，删车时由同步任务清理孤儿行。
    fms_vehicle_id: Mapped[int | None] = mapped_column(Integer, index=True)
    plate_no: Mapped[str] = mapped_column(String(32), default="")
    apply_no: Mapped[str] = mapped_column(String(32), default="")
    out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    out_mileage: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_mileage: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_name: Mapped[str] = mapped_column(String(16), default="")
    applicant_name: Mapped[str] = mapped_column(String(32), default="")
    dept_name: Mapped[str] = mapped_column(String(64), default="")
    destination: Mapped[str] = mapped_column(String(255), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = {"comment": "che1 派车记录"}


class Che1Fuel(Base):
    """che1 加油记录。"""
    __tablename__ = "che1_fuel_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_id: Mapped[str] = mapped_column(String(64), unique=True)
    che1_vehicle_id: Mapped[int] = mapped_column(ForeignKey("che1_vehicles.id"), index=True)
    fms_vehicle_id: Mapped[int | None] = mapped_column(Integer, index=True)
    plate_no: Mapped[str] = mapped_column(String(32), default="")
    fuel_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    mileage: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0, comment="加油量（升）")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal('0'), comment="金额（元）")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = {"comment": "che1 加油记录"}


class Che1SyncRun(Base):
    """che1 同步执行记录（手动 / 定时）。"""
    __tablename__ = "che1_sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", comment="manual/scheduled")
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = {"comment": "che1 同步执行记录"}


# 通知统一由 app.core.notify.notify 提供（不再在此重复定义，避免两套签名
# 与事务语义不一致；查重/邮件兜底/不破坏调用方事务的实现见 core/notify.py）。

__all__ = [
    "Base", "now", "utcnow_naive", "alive", "EPOCH", "EPOCH_PREFIX",
    "Department", "Supplier", "VehicleType", "VehicleNature", "Vehicle",
    "User", "Role", "RolePermission", "UserRole",
    "Contract", "ContractVehicleType", "ContractItem",
    "FlowTemplate", "RepairOrder", "RepairAction", "RepairQuote",
    "RepairQuoteItem", "RepairCC", "Attachment",
    "DailyCheckTemplate", "DailyCheckRecord", "DailyCheckPhoto",
    "AuditLog", "AiUsage", "AppSetting", "PasswordReset",
    "BackupTarget", "BackupJob", "BackupRun", "BackupItem", "BackupRestoreRun",
    "LoginThrottle", "Notification", "SeqCounter", "AppRelease", "TempVehicleUse",
    "FencePoint", "FenceAlert", "FenceNightRun", "GpsCache", "ApiKey",
    "Che1Vehicle", "Che1Usage", "Che1Fuel", "Che1SyncRun",
    "notify",
]
