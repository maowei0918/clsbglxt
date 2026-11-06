#!/usr/bin/env python3
"""软删除哨兵专项测试：验证 alive() 过滤 + 删除后不可见 + notify 事务边界。"""
import sys, uuid, os
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import warnings; warnings.filterwarnings("ignore")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import (Base, EPOCH, alive, utcnow_naive, DailyCheckRecord,
                        Notification, User)
from app.core.notify import notify

DB = f"/tmp/softdel_{uuid.uuid4().hex[:8]}.db"
e = create_engine(f"sqlite:///{DB}")
Base.metadata.create_all(e)
S = sessionmaker(bind=e, autoflush=False, expire_on_commit=False)

ok = 0; fail = 0
def check(name, cond):
    global ok, fail
    if cond: ok += 1; print(f"  ✓ {name}")
    else: fail += 1; print(f"  ✗ {name}")

print("=== A. alive() 过滤 ===")
s = S()
r = DailyCheckRecord(vehicle_id=1, user_id=1, check_date=date(2026, 1, 1),
                     results="[]", has_abnormal=False)
s.add(r); s.commit()
check("未删除时 alive=1", s.query(DailyCheckRecord).filter(alive(DailyCheckRecord.deleted_at)).count() == 1)
check("未删除时 is_(None)=0（原 bug 根因）", s.query(DailyCheckRecord).filter(DailyCheckRecord.deleted_at.is_(None)).count() == 0)

print("=== B. 删除后不可见 ===")
r.deleted_at = utcnow_naive(); s.commit()
check("删除后 alive=0", s.query(DailyCheckRecord).filter(alive(DailyCheckRecord.deleted_at)).count() == 0)
check("默认哨兵等于 EPOCH", r.deleted_at != EPOCH)

print("=== C. notify() 不破坏事务边界 ===")
n = notify(s, 999, "t", "b", kind="test", target_type="x", target_id=1)
check("notify 返回对象且 add 到 session", n is not None and n in s.new)
s.rollback()  # 模拟业务失败回滚
check("回滚后通知未落库", s.query(Notification).count() == 0)
# 正常提交路径
n2 = notify(s, 999, "t", "b", kind="test", target_type="x", target_id=2)
s.commit()
check("显式 commit 后通知落库", s.query(Notification).count() == 1)
# 查重
n3 = notify(s, 999, "t", "b", kind="test", target_type="x", target_id=2)
check("10 分钟内同 target 去重", n3 is None)

s.close(); os.remove(DB)
print(f"\n=== 结果: {ok} 通过 / {fail} 失败 ===")
sys.exit(1 if fail else 0)
