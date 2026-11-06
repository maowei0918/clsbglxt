# scripts/

## mutation_check.py — 测试有效性自检

```bash
cd server
.venv/bin/python scripts/mutation_check.py            # 跑全部变异
.venv/bin/python scripts/mutation_check.py --list     # 列出用例
.venv/bin/python scripts/mutation_check.py --only repairs_claim_cas
```

**它解决什么问题**：单元测试全绿，只说明「当前实现能通过测试」，不说明
「测试能在实现出错时变红」。本工具对生产代码施加一处刻意的有害改动，
期望对应测试失败（称为「杀死变异」）。若测试仍绿，说明存在盲区，需补强。

**何时跑**：
- 新增并发 / 幂等 / 权限 / 状态机类测试后
- 重构了被测试的实现（anchor 可能失效，脚本会报错提示同步更新）
- 发版前的深度检查

**不进 `pytest tests/`**：脚本会临时改写生产代码文件。虽然自带备份 +
`finally` 无条件还原 + 逐字节校验，但与常规测试的只读语义冲突，故作为
独立手动入口，不纳入 CI 自动执行。

**新增用例**：编辑 `_build_mutations()`，填 `target` / `original`（必须与文件
逐字一致且唯一）/ `mutated` / `tests`。也可用 `--mutation-file cases.json`
临时传入，格式为同名字段的对象数组。

**退出码**：`0` = 全部变异被杀死；`1` = 有存活（测试盲区）或执行/还原异常。

### 当前用例

| name | 缺陷模型 | 守护测试 |
| --- | --- | --- |
| `repairs_claim_cas` | 抢单从 CAS 条件更新退化成「先读后写 + sleep」的 TOCTOU | `test_claim_single_winner_under_concurrency` |

---

## 测试隔离契约（重要）

脚本式测试（`tests/test_e2e.py` 等）会调用真实的 `/api/install/save`，
它写 `.env` 与安装标记。这些路径必须隔离到临时目录，否则会改写生产
`server/.env`——`JWT_SECRET` 一旦被轮换，所有在线用户 token 立即失效。

**唯一正确的隔离方式**：

```python
import app.installer as inst
inst.configure(data_dir=_TMP, env_file=_TMP / "install.env")
```

一次调用同步 `data_dir` / `install_lock` / `install_running` /
`install_rl` / `env_file` 五个路径（三个安装标记由 `data_dir` 派生）。

**禁止**：`inst.ENV_FILE = ...`、`inst.DATA_DIR = ...` 这类直接赋值。
它只能覆盖你手写的那一个，漏掉新增的派生路径，正是历史事故的根因。

**守护**：`tests/test_scripts_regression.py` 提供三层校验
1. 每个脚本执行前后比对 `.env` 快照，定位越界脚本
2. 模块级基准快照在全部脚本跑完后终检，防止累积掩盖
3. 契约测试：脚本必须调用 `configure()`、禁止直接赋值；且 `configure()`
   必须覆盖 `_paths` 里的每个 key（新增路径不漏）

新增安装相关路径时，只需加到 `installer._paths` 与 `configure()`，
所有测试自动隔离，无需逐个改脚本。
