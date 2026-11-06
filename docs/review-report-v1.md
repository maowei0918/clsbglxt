# clglxt 后端审查报告（方案 D）

**审查时间**：2026-11-02  
**范围**：13 个业务模块 + core 基件（deps / audit / security / database）  
**结论**：核心链路能跑通，但有 **5 个 P0**（会导致越权/数据错误/500），必须在部署前修掉。

---

## P0 — 必须修（会导致越权、数据错乱或稳定 500）

### P0-1 审计完全没落地
`main.py` 的 `audit_mutations` 中间件是空壳，只判断了路径就 `return`，从没写 `AuditLog`；13 个业务模块也**没有一处**调用 `audit()`。等于审计系统零记录。

**影响**：删车、删合同、改角色、重置密码、停用汽修厂这些高危操作全部无痕，出事无法追责。

### P0-2 `list_orders` 汽修厂越权看全公司维修单
```python
if scope == "mine": ...
elif current_user.department_id and not current_user.is_superuser:
    q = q.join(...).filter(Vehicle.department_id == current_user.department_id)
```
汽修厂账号 `department_id` 为空 → 两个分支都不进 → **默认 `scope=all` 能看到所有维修单**。必须按 `target_supplier_id == user.supplier_id`（或服务范围）过滤。

### P0-3 `submit_quote` 报价明细全部挂错（`q.id` 为 None）
```python
q = RepairQuote(...)          # 还没 add/flush
for it in items:
    db.add(RepairQuoteItem(quote_id=q.id, ...))   # q.id 是 None！
db.add(q)
```
`q.id` 在 flush 前是 `None`，所有明细 `quote_id=None` → 非空约束报错（或产生孤儿行）。必须在循环前 `db.add(q); db.flush()`。

### P0-4 `advance_order` 的 CAS 是假的（并发串状态）
```python
o = db.get(RepairOrder, order_id)
if seen_seq and o.current_seq != seen_seq: 409   # seen_seq=0 时直接跳过
o.current_seq += 1                                # 读后自增，无锁
```
注释写"用 SELECT FOR UPDATE"，代码里根本没有 `.with_for_update()`。默认 `seen_seq=0` 时并发两个请求都通过，`current_seq` 丢失更新。100-1000 单/天必须修。

### P0-5 `fence.py` 缺 `import json`
`add_fence_point` 里 `json.loads(shop.dept_ids)`，但文件顶部没有 `import json`。汽修厂账号一旦调用必 `NameError` 500。

---

## P1 — 高危（并发/权限/业务语义）

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| P1-1 | `repairs._gen_order_no` | 查 `max(order_no)+1`，并发撞号 → 唯一约束 500 | 用 `seq_counters` 表原子自增 |
| P1-2 | `repairs.date_to` | `created_at <= date_to` 漏掉当天数据 | 改为 `< date_to + 1 天` |
| P1-3 | `create_order` | 只校验汽修厂存在/启用，没校验它**服务该部门** | 查 `serves_all_depts` / `dept_ids` |
| P1-4 | `claim_quote` | 不调 `_check_view`、不校验报价权限 | 加 `repair:quote` + 汽修厂归属校验 |
| P1-5 | `submit_quote` | 同上，内部账号也能报价 | 同上 |
| P1-6 | `cancel_order` | 能看就能取消，同部门他人都能取消别人单子 | 限提报人 / `repair:manage` |
| P1-7 | `daily_check.list_records` | 无部门隔离，跨部门可见 | 按车辆部门过滤 |
| P1-8 | `vehicle_use.create_use` | 可传任意 `department_id`，越权备案到别的部门 | 非超管强制本部门 |
| P1-9 | `create_order` 幂等 | TOCTOU：并发同 idem_key 第二个请求 500 | catch IntegrityError 回查已有单 |

---

## P2 — 中危（健壮性）

- `daily_check`：`results=str(results)` 存 Python repr，建议 `json.dumps`
- `daily_check`：`ai_review_daily_check.delay()` 无 try/except，Celery 挂了会 500（记录已提交）
- `personnel.reset_password`：只有 `system:user_manage` 就能重置任意账号，缺归属校验
- `suppliers.dept_ids`：`str(list)` 存库，与 `json.loads` 读取不对称，改 `json.dumps`
- `my_shop.delete_my_user`：只 `is_active=False` 没软删，与 personnel 语义不一致

---

## 已确认没问题（复核过，不误报）

- `claim_quote` 的 `UPDATE ... WHERE quote_owner_id IS NULL` 是**有效** CAS，并发安全
- `order_no`、`(reporter_id, idem_key)` 唯一约束都在 models 里，数据层有兜底
- 登录限频失败降级放行、`verify_dummy` 防枚举、`token_version` 失效机制均正确

---

## 修复顺序

1. P0-5（一行）→ P0-3 → P0-2 → P0-4 → P0-1（审计）
2. P1 全量
3. P2 择机
4. Alembic 迁移脚本替代 `create_all`
