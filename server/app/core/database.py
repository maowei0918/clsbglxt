"""数据库引擎与 Session。PostgreSQL 版。

与 fms(SQLite) 的核心差异：
  * 连接池：pool_size + max_overflow 控制并发连接数
  * 时区：列类型统一 DateTime(timezone=True)，存 UTC
  * 软删除：PG 支持 NULL 唯一性处理（NULLS NOT DISTINCT 可选），
    但我们仍沿用 EPOCH 哨兵值，保持与 fms 的数据语义一致，便于 API 导入。
"""
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import (DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_RECYCLE, DB_POOL_SIZE,
                     DB_POOL_TIMEOUT)

log = logging.getLogger("clglxt.db")

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


def health_check() -> bool:
    """真正执行一次查询，验证连接池可用。"""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("数据库健康检查失败")
        return False
