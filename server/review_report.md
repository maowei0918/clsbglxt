# clglxt 后端审查报告（方案 D）

**审查人**：Ops · 运维工程师  
**审查时间**：2026-11-02  
**审查范围**：所有 13 个业务模块 + 核心基件  
**结论**：核心逻辑可生产运行，但存在 2 个 P0 + 5 个 P1 问题，需在部署前修复。

---

## 1. 审计中间件（P0 必须修）

`app/main.py` 的 `audit_mutations` 只是空壳：

```python
@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    response = await call_next(request)
    # TODO: 写 AuditLog
    return response
```

**风险**：无审计记录，生产运维无法追责。

**修复**（推荐）：

```python
from app.core.audit import audit_request, build_ctx
# ...

async def audit_mutations(request: Request, call_next):
    ctx = build_ctx(request)
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/api/"):
        audit_request(
            db=... ,  # 需要注入 db
            ctx=ctx,
            user_id=0,
            method=request.method,
            path=path,
            status=response.status_code,
            detail=...,  # 可从 response.body 解析
        )
    return response
```

---

## 2. 抢单 CAS 并发（repairs2.py）

```python
# claim_quote
updated = (db.query(RepairOrder)
           .filter(RepairOrder.id == order_id, RepairOrder.quote_owner_id.is_(None))
           .update({RepairOrder.quote_owner_id: current_user.id}, synchronize_session=False))
```

**问题**：`synchronize_session=False` + `update` 不能正确更新对象，后面 `o.quote_owner_id` 仍为旧值。

**风险**：高并发抢单时可能重复抢。

**修复**：

```python
# 正确方式
o = db.get(RepairOrder, order_id)
if o and o.quote_owner_id is None:
    o.quote_owner_id = current_user.id
    db.commit()
```

---

## 3. fence.py 缺少 import json

```python
# 缺少这一行
import json
```

**风险**：`dept_ids = json.loads(shop.dept_ids or "[]")` 会 NameError。

---

## 4. 部门隔离逻辑不一致（多个模块）

- `repairs.py`：有 `scope=="mine"` 逻辑，但 `department_id` 过滤用的是 `Vehicle.department_id`
- `fence.py`：只检查 `v.department_id`
- `vehicle_account.py`：只检查 `v.department_id`

**风险**：汽修厂账号（supplier_id 不为空）能看全公司数据。

**修复**：统一用 `DeptChecker.can_see_dept`

---

## 5. 权限收紧（P1）

- `require_perm("daily_check:manage")` 写死在模板操作
- `my_shop` 白名单 `ASSIGNABLE` 没有 "repair:approve" 等
- `vehicle_account` 不校验权限（应该加）

**建议**：增加 `vehicle_account:view`、`fence:manage` 等权限点。

---

## 6. 单号并发（P1）

`order_no` 唯一约束，但 `_gen_order_no` 用 `like` + `int(max_val[len(prefix):])` 不可靠。

**修复**：用 PG 序列或 Alembic 自动生成。

---

## 7. 数据完整性（P1）

- `daily_check` 提交记录时没校验模板 `items` 长度
- `vehicle_use` 没处理 `end_date` 回车逻辑
- `fence` 没处理 `radius_m` 边界校验

---

## 8. 其他小问题

- `import_fms.py` 占位接口返回 501
- `my_shop.py` 汽修厂建号没校验汽修厂管理员身份
- `personnel.py` 改号时没更新 `token_version`

---

## 修复优先级

| 优先级 | 问题 | 风险 | 建议 |
|---|---|---|---|
| P0 | 审计中间件 | 中 | 必须 |
| P0 | 抢单 CAS | 高 | 必须 |
| P0 | fence.py json | 中 | 必须 |
| P1 | 部门隔离不统一 | 中 | 改 |
| P1 | 单号并发 | 中 | 改 |
| P1 | 权限收紧 | 低 | 改 |

---

## 计划

1. 完成所有 P0 修复（本周内）
2. 补充 Alembic 迁移脚本（`server/alembic/versions/`）
3. 跑一次生产就绪性检查（`alembic revision --autogenerate`）
4. 部署前把 review 报告发给用户确认

---

**附件**：全部代码已读完，报告已写完。需要我现在直接修 P0 还是先看其他？
