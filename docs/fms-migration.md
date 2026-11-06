# FMS → clglxt 数据迁移

> 一次性迁移，FMS 停机前实施。决策依据见 `deploy-plan.md`：
> **通过老 FMS API 拉取导入，不走双写，不先复刻 SQLite。**

## 1. 数据源契约

老 FMS v2 对外只读开放接口（`fms/v2/server/app/v2/openapi.py`）：

| 项目 | 值 |
| --- | --- |
| 基址 | `CLGLXT_FMS_BASE_URL`（如 `https://fms.example.com`） |
| 鉴权 | `Authorization: Bearer fms_xxx`（明文密钥只在创建时返回一次） |
| 端点 | `GET /api/v2/open/{vehicles,repairs,vehicle-uses,daily-checks,contracts}` |
| 分页 | `offset` / `limit`（上限 500），响应 `{items, total, has_more, next_offset}` |
| 限频 | 每密钥 60 次/分钟（FMS 侧进程内计数，429 会自动指数退避重试） |
| scope | `vehicles:read` / `repairs:read` / `vehicle_use:read` / `daily_check:read` / `contracts:read` |

**密钥创建**：FMS 后台 → 系统管理员 → `/api/v2/open/keys`（`POST`），
`scopes` 传上述全部 5 项。库内只存 SHA-256 hash，明文只返回一次。

## 2. 配置

在 `server/.env` 中填写（改完**重启站点**让配置生效）：

```dotenv
CLGLXT_FMS_BASE_URL=https://fms.example.com
CLGLXT_FMS_API_KEY=fms_xxxxxxxxxxxxxxxx
CLGLXT_SSRF_ALLOWED_HOSTS=fms.example.com
CLGLXT_FMS_PAGE_SIZE=200
```

> `CLGLXT_SSRF_ALLOWED_HOSTS` 是出站白名单——FMS 基址的 **host（含端口）**
> 必须在名单内，否则客户端拒绝发起请求。这是防止把密钥发往任意地址。

## 3. API（需登录，管理员权限）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/import-fms/config` | 当前配置（`base_url` / 是否已配密钥 / 单页条数） |
| `GET` | `/api/import-fms/status` | 查询进度：`state` / `total` / `done` / `current_step` / `errors` |
| `POST` | `/api/import-fms/test` | 连通性自检（拉 1 条车辆，不写库） |
| `POST` | `/api/import-fms/start` | 启动 Celery 异步导入；body 可选 `{"scopes":[...], "batch_size":100}` |
| `POST` | `/api/import-fms/stop` | 请求停止（在批次边界生效，已提交数据保留） |
| `POST` | `/api/import-fms/reset` | 清进度重来；body `{"keep_map":true}` 保留 ID 映射 |

除 `status` 外均需 `system:setting_backup` 权限（超管默认拥有）。

启动前**必须**先跑 `test`：它会校验基址、密钥、白名单、scope 是否齐全。

## 4. 导入策略

- **不保留 FMS 原始 ID**：clglxt 内置角色/超管走显式 ID（1/2/3），沿用会撞主键
  且需要事后 `sync_sequences`。改用业务键幂等：
  `plate_no`（车辆）、`order_no`（维修单）、`name+supplier`（合同）、
  `name`（部门/车型）、`plate_no+start_date`（临时用车）。
- **跨库 ID 映射**：FMS 的 `id → clglxt 的 id` 存 Redis
  `clglxt:import:map:{scope}`，维修单/日检的 `vehicle_id` 据此翻译；
  映射缺失时按车牌回查。
- **分批 + 断点续传**：每 `batch_size` 条 commit 一次，进度
  （`offset_{scope}` / `done_{scope}`）落 `clglxt:import:status`（24h）。
  中断后重跑 `start` 会从上次 offset 继续，已提交的行不重做。
- **失败恢复**：单条脏数据（缺车牌、非法日期、悬空外键）跳过并记入
  `errors`（最多 200 条），不中断整批；scope 级异常记录后继续下一个。
- **停止**：`stop` 写 Redis 取消信号，任务在批次边界退出，已提交的批次保留。

导入顺序（外键依赖）：`vehicles → contracts → repairs → vehicle_uses → daily_checks`。

## 5. 字段映射与已知限制

clglxt 与 FMS v2 的 `models.py` **45 张表同构**，但开放接口只暴露
**业务摘要字段**（不是整表 dump）：

| 端点 | 可迁字段 | 落到 clglxt |
| --- | --- | --- |
| `vehicles` | `id, plate_no, vehicle_type_name, nature, department_name, brand, status` | `vehicles` + 按名称 get-or-create `vehicle_types` / `departments` |
| `repairs` | `id, order_no, vehicle_id, vehicle_plate, status, total_amount, created_at` | `repair_orders`（摘要级） |
| `vehicle-uses` | `id, plate_no, type_name, nature, dept_name, start_date, end_date, reason` | `temp_vehicle_uses` |
| `daily-checks` | `id, vehicle_id, vehicle_plate, check_date, has_abnormal, reporter_name, created_at` | `daily_check_records`（`results` 写 `[]` 占位） |
| `contracts` | `id, name, supplier, is_active, start_date, end_date` | `contracts` |

**开放接口拿不到、因此本次不迁的数据**：

- 维修单明细：`repair_actions`（审批流）、`repair_quotes` / `repair_quote_items`
  （报价明细）、`attachments`（附件文件）、`repair_cc`
- 用户 / 角色 / 权限（`users` / `roles` / `role_permissions` / `user_roles`）
- 合同明细 `contract_items` / `contract_vehicle_types`、流程模板 `flow_templates`
- 审计日志、通知、备份配置等运行期数据
- 车辆附件物理文件（FMS `data/uploads/`）

> 导入数据的 `reporter_id` / `created_by` / `created_by` 统一落到 clglxt 的
> 超管账号（占位），**不迁移 FMS 用户体系**。上线后由管理员重新分配角色。

**如需完整迁移**（含报价明细、审批流、附件），两条路：
1. FMS 停机后直读 `fms_v2.db`（SQLite，45 表），写一次性 ETL 脚本；
2. 扩展 FMS 开放接口增加明细端点。
两者都超出当前「开放 API 摘要导入」的范围，需单独评估。

## 6. 验收

1. `POST /api/import-fms/test` → `{"ok": true}`
2. `POST /api/import-fms/start` → 轮询 `status` 直到 `state == "done"`
3. 核对 `created + updated + skipped == total`，`errors` 逐条确认是可接受的脏数据
4. 抽查车辆数、维修单数与 FMS 侧 `total` 对齐；车牌/单号无重复
5. 重复跑一次 `start`：`created == 0`、总行数不变（幂等）

单元测试：`server/tests/test_import_fms.py`（9 项，覆盖字段映射 / 幂等 /
断点续传 / 脏数据 / 取消 / 状态契约）。

## 7. 回滚

导入只做**幂等 upsert**，不删 clglxt 现有数据。若要清除本次导入结果：
用 `deploy/update.sh` 的自动备份做整库恢复，或按 `clglxt:import:map:*`
里的本地 ID 精确删除。操作前先备份。
