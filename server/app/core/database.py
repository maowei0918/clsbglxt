"""数据库引擎与 Session。PostgreSQL 版。

与 fms(SQLite) 的核心差异：
  * 连接池：pool_size + max_overflow 控制并发连接数
  * 时区：列类型统一 DateTime(timezone=True)，存 UTC
  * 软删除：PG 支持 NULL 唯一性处理（NULLS NOT DISTINCT 可选），
    但我们仍沿用 EPOCH 哨兵值，保持与 fms 的数据语义一致，便于 API 导入。
"""
import logging
import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import (DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_RECYCLE, DB_POOL_SIZE,
                     DB_POOL_TIMEOUT)

log = logging.getLogger("clglxt.db")

# sync_sequences 里要把系统目录读到的表名/列名拼进 SQL（setval 的
# regclass 参数无法用绑定参数），先做一次白名单校验防意外注入。
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

_IS_SQLITE = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {
    "pool_pre_ping": True,
    "echo": False,
    "future": True,
}
if not _IS_SQLITE:
    # PostgreSQL：连接池与超时控制
    _engine_kwargs.update(
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT,
        pool_recycle=DB_POOL_RECYCLE,
    )

_engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(_engine, "connect")
def _on_connect(dbapi_conn, _record):
    """连接建立时的会话级设置（仅 PostgreSQL）。

    statement_timeout：单条 SQL 最长执行时间，避免慢查询卡死整个连接池。
    idle_in_transaction_session_timeout：事务空闲超时，防止忘记 commit 的连接长期占着。
    """
    if _IS_SQLITE:
        return
    with dbapi_conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30s'")
        cur.execute("SET idle_in_transaction_session_timeout = '60s'")
        cur.execute("SET application_name = 'clglxt'")
    dbapi_conn.commit()


def get_db():
    """FastAPI 依赖：每请求一个 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表（开发/首次启动用）。生产环境用 Alembic 迁移。"""
    from .. import models  # noqa: F401  确保模型已注册
    Base.metadata.create_all(bind=_engine)
    log.info("数据库表已就绪")


def sync_sequences(engine=None) -> None:
    """把 PostgreSQL 自增序列对齐到各表当前 max(id)。

    内置角色（id=1/2/3）、超管等走显式 id 插入，PostgreSQL 的
    SERIAL/IDENTITY 序列不会随之推进。若不校正，安装后第一次通过
    API 建角色/建号就会撞 roles_pkey / users_pkey 之类的主键冲突。

    engine 缺省用全局 _engine；安装流程传入刚建好的临时 engine。
    仅在 PostgreSQL 生效（SQLite 无需处理）；幂等，可重复执行。
    """
    eng = engine or _engine
    if eng.dialect.name == "sqlite":
        return

    # 同时覆盖两种自增实现：
    #   * SERIAL  → 列默认值里带 nextval(...)
    #   * IDENTITY → attidentity 为 'a'(ALWAYS) / 'd'(BY DEFAULT)
    # 只匹配 nextval 会漏掉 GENERATED AS IDENTITY 的列。
    # pg_table_is_visible 尊重 search_path，比写死 current_schema() 稳。
    _DISCOVER = """
    SELECT c.relname, a.attname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
    WHERE c.relkind = 'r'
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND pg_table_is_visible(c.oid)
      AND (
            a.attidentity IN ('a', 'd')
         OR (d.adbin IS NOT NULL
             AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%')
      )
    """

    with eng.begin() as conn:
        rows = conn.execute(text(_DISCOVER)).fetchall()
        for table, col in rows:
            # 表名/列名来自系统目录，理论安全；仍做白名单校验，
            # 因为下面要把它拼进 SQL 字符串（setval 的 regclass 参数
            # 只接受常量名，无法用绑定参数）。
            if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(col):
                log.warning("跳过可疑标识符：%r.%r", table, col)
                continue
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table, "c": col},
            ).scalar()
            if not seq:
                continue

            # 只前进不回退：若序列已领先（例如补过数据、删过行），
            # 无脑 setval(max+1) 会让 ID 复用，撞软删行/外键。
            # 先取序列当前逻辑位置（is_called=false 时 last_value 尚未发出），
            # 与 max(id)+1 取大者。
            pos = conn.execute(
                text("SELECT last_value, is_called FROM " + seq)
            ).first()
            seq_next = 1 if (pos is None or not pos[1]) else int(pos[0]) + 1
            table_next = conn.execute(
                text('SELECT COALESCE(MAX("%s"), 0) + 1 FROM "%s"'
                     % (col, table))
            ).scalar() or 1

            if table_next <= seq_next:
                continue  # 序列已覆盖，无需动
            conn.execute(
                text("SELECT setval(:seq, :v, false)"),
                {"seq": seq, "v": table_next},
            )
    log.info("PostgreSQL 序列已同步到各表 max(id)")


def health_check() -> bool:
    """真正执行一次查询，验证连接池可用。"""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("数据库健康检查失败")
        return False
