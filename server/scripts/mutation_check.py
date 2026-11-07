#!/usr/bin/env python3
"""变异测试（mutation testing）自检工具。

目的：证明「测试真的能抓到缺陷」，而不是攒一堆永远绿的假测试。

做法：对生产代码施加一处刻意的有害改动（mutation），跑指定测试，期望测试
失败（变红）。测试变红 => 该测试对这个缺陷是敏感的，变异被"杀死"。

与单元测试的区别：
  * 单元测试问「实现是否正确」
  * 变异测试问「测试是否有效」——它检验的是测试套件本身的质量。

安全保证（本仓库不是 git 仓库，因此不依赖 git 还原）：
  1. 动手前把目标文件备份到临时目录
  2. 校验 mutation 的 anchor 唯一存在，避免模糊替换
  3. finally 中无条件还原，并逐字节比对还原后的内容与备份一致
  4. 退出前再自检一次：若还原失败，以非零码退出并大声报错
     （绝不静默把变异留在工作区）

用法：
    # 用例名 => (目标文件, 原始片段, 变异片段)
    # 放在 MUTATIONS 里，或直接用 --mutation-file 传入 JSON

    .venv/bin/python scripts/mutation_check.py

    # 只跑某个用例
    .venv/bin/python scripts/mutation_check.py --only repairs_claim_cas

    # 列出所有用例
    .venv/bin/python scripts/mutation_check.py --list

退出码：
    0  所有变异都被杀死
    1  有变异存活（测试有盲区）或执行/还原异常
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = SERVER_DIR / ".venv" / "bin" / "python"

# 跑测试时的干净环境：清掉 PYTHONPATH（外部 site-packages 会污染，
# 例如 pydantic_core 二进制损坏），并禁用 pytest 插件自动加载。
TEST_ENV = {
    "PYTHONPATH": "",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}


@dataclass
class Mutation:
    name: str
    target: str          # 相对 SERVER_DIR 的路径
    original: str        # 必须唯一存在的原始片段
    mutated: str         # 替换后的有害版本
    tests: list[str]     # 期望变红的测试 node id


def _build_mutations() -> list[Mutation]:
    """内置变异用例。新增用例时在这里追加。

    original 必须与目标文件中的文本**逐字**一致（含缩进与空行）。
    建议用 `grep -n` 或 read_file 先确认，改完代码后也要同步更新。
    """
    repairs = "app/modules/repairs2.py"

    return [
        # ── 安全防护变异：证明测试真的能拦住对应缺陷 ──
        Mutation(
            name="must_change_check_removed",
            target="app/core/deps.py",
            original=(
                "    if user.must_change_password and request.url.path not in MUST_CHANGE_ALLOWED:\n"
                "        raise HTTPException(\n"
                "            403, \"首次登录须先修改密码：请调用 /api/auth/change-password\")"
            ),
            mutated=(
                "    if False and user.must_change_password:\n"
                "        raise HTTPException(\n"
                "            403, \"首次登录须先修改密码：请调用 /api/auth/change-password\")"
            ),
            tests=["tests/test_security_batch4.py::test_must_change_password_blocks_business_api"],
        ),
        Mutation(
            name="backup_basepath_webdir_allowed",
            target="app/modules/backup.py",
            original=(
                "        if p == bad or bad in p.parents:\n"
                "            raise HTTPException(400, f\"备份目录不得位于 {bad} 之下\")"
            ),
            mutated=(
                "        if False:\n"
                "            raise HTTPException(400, f\"备份目录不得位于 {bad} 之下\")"
            ),
            tests=["tests/test_security_batch4.py::test_backup_base_path_validation"],
        ),
        Mutation(
            name="pixel_guard_removed",
            target="app/core/image_utils.py",
            original="            _pixel_guard(im)",
            mutated="            pass  # [MUTATION] 预检被移除",
            tests=["tests/test_security_batch4.py::test_decompression_bomb_rejected_before_decode"],
        ),
        Mutation(
            name="rate_limit_fails_open",
            target="app/core/redis_client.py",
            original=(
                "    except Exception:\n"
                "        log.warning(\"Redis 限频失败 key=%s，降级为进程内限频\", key,\n"
                "                    exc_info=True)\n"
                "        return _local_rate_limit(key, limit, window)"
            ),
            mutated=(
                "    except Exception:\n"
                "        return True, limit  # [MUTATION] Redis 挂了直接放行"
            ),
            tests=["tests/test_security_batch4.py::test_rate_limit_degrades_closed_when_redis_down"],
        ),
        Mutation(
            name="audit_ip_trusts_any_xff",
            target="app/core/audit.py",
            original="    if direct in TRUSTED_PROXIES:\n        xff = request.headers.get(\"x-forwarded-for\", \"\")",
            mutated="    if True:  # [MUTATION] 无条件信任 XFF\n        xff = request.headers.get(\"x-forwarded-for\", \"\")",
            tests=["tests/test_security_batch4.py::test_audit_client_ip_ignores_xff_from_untrusted"],
        ),
        Mutation(
            name="upload_path_traversal_allowed",
            target="app/core/image_utils.py",
            original=(
                "    if rp != base and base not in rp.parents:\n"
                "        raise ValueError(\"非法的存储路径\")"
            ),
            mutated=(
                "    if False:\n"
                "        raise ValueError(\"非法的存储路径\")"
            ),
            tests=["tests/test_security_batch4.py::test_upload_path_traversal_blocked"],
        ),
        Mutation(
            name="att_path_validation_removed",
            target="app/modules/repairs.py",
            original=(
                "    if not raw_path or \"\\x00\" in raw_path or Path(raw_path).is_absolute():\n"
                "        raise HTTPException(400, \"附件路径非法\")\n"
                "    if \"..\" in Path(raw_path).parts:\n"
                "        raise HTTPException(400, \"附件路径非法\")\n"
                "    return True"
            ),
            mutated=(
                "    return True  # [MUTATION] 校验被移除"
            ),
            tests=["tests/test_security_batch4.py::test_attachment_rejects_client_paths"],
        ),

        Mutation(
            name="repairs_claim_cas",
            target=repairs,
            original=(
                "    # 条件更新 CAS\n"
                "    updated = (db.query(RepairOrder)\n"
                "               .filter(RepairOrder.id == order_id, RepairOrder.quote_owner_id.is_(None))\n"
                "               .update({RepairOrder.quote_owner_id: current_user.id},\n"
                "                       synchronize_session=False))\n"
                "    if not updated:\n"
                "        raise HTTPException(409, \"抢单失败，该维修单已被他人抢单\")\n"
                "\n"
                "    db.commit()"
            ),
            mutated=(
                "    # [MUTATION] 故意的 TOCTOU：先读后写，无条件覆盖\n"
                "    import time as _t\n"
                "    _t.sleep(0.05)\n"
                "    db.query(RepairOrder).filter(\n"
                "        RepairOrder.id == order_id\n"
                "    ).update({RepairOrder.quote_owner_id: current_user.id},\n"
                "              synchronize_session=False)\n"
                "\n"
                "    db.commit()"
            ),
            tests=["tests/test_repairs_claim_concurrency.py::test_claim_single_winner_under_concurrency"],
        ),
    ]


def _load_from_json(path: Path) -> list[Mutation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Mutation(**item) for item in raw]


def _resolve_python() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def _run_tests(tests: list[str]) -> tuple[int, str]:
    """在干净环境下跑测试，返回 (returncode, 尾部输出)。"""
    env = os.environ.copy()
    env.update(TEST_ENV)
    cmd = [_resolve_python(), "-m", "pytest", *tests, "-q", "--tb=line", "-x"]
    proc = subprocess.run(
        cmd, cwd=str(SERVER_DIR), env=env,
        capture_output=True, text=True, timeout=600,
    )
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
    return proc.returncode, tail


def _apply_and_restore(target: Path, original: str, mutated: str,
                       backup_dir: Path) -> tuple[bool, str]:
    """写入变异 -> 返回 (成功, 错误信息)。还原与校验由调用方负责。"""
    text = target.read_text(encoding="utf-8")
    count = text.count(original)
    if count != 1:
        return False, (
            f"anchor 出现 {count} 次（要求恰好 1 次）。"
            f"请修正 mutation 定义，避免模糊替换。"
        )
    target.write_text(text.replace(original, mutated), encoding="utf-8")
    return True, ""


def _restore(target: Path, backup: Path, original_bytes: bytes) -> str | None:
    """把文件还原为原始内容。成功返回 None，失败返回错误描述。

    双重还原：先写原始字节，再校验；不一致时从备份覆盖，再校验。
    任何一步抛异常都要转成字符串返回，不能让它逃出 finally 的调用点。
    """
    try:
        target.write_bytes(original_bytes)
    except Exception as e:
        return f"写入原始内容失败：{type(e).__name__}: {e}"
    if target.read_bytes() == original_bytes:
        return None
    try:
        shutil.copy2(backup, target)
    except Exception as e:
        return f"从备份 {backup} 恢复失败：{type(e).__name__}: {e}"
    if target.read_bytes() == original_bytes:
        return None
    return f"备份 {backup} 也无法还原，请立即手动恢复。"


def main() -> int:
    ap = argparse.ArgumentParser(description="变异测试自检")
    ap.add_argument("--list", action="store_true", help="列出内置变异用例")
    ap.add_argument("--only", default=None, help="只跑指定名字的变异")
    ap.add_argument("--mutation-file", default=None,
                    help="从 JSON 文件加载变异用例（覆盖内置）")
    args = ap.parse_args()

    mutations = (_load_from_json(Path(args.mutation_file))
                 if args.mutation_file else _build_mutations())

    if args.list:
        for m in mutations:
            print(f"{m.name:<32} -> {m.target}  ({len(m.tests)} test(s))")
        return 0

    if args.only:
        mutations = [m for m in mutations if m.name == args.only]
        if not mutations:
            print(f"错误：找不到变异用例 {args.only!r}", file=sys.stderr)
            return 1

    print(f"变异自检：共 {len(mutations)} 个用例")
    print("=" * 72)

    backup_dir = Path(tempfile.mkdtemp(prefix="clglxt_mutbak_"))
    killed: list[str] = []
    survived: list[str] = []
    errored: list[str] = []

    try:
        for m in mutations:
            target = SERVER_DIR / m.target
            if not target.exists():
                print(f"[错误] {m.name}: 目标文件不存在 {m.target}", file=sys.stderr)
                errored.append(m.name)
                continue

            backup = backup_dir / m.target.replace("/", "__")
            shutil.copy2(target, backup)
            original_bytes = target.read_bytes()

            ok, err = _apply_and_restore(target, m.original, m.mutated, backup_dir)
            if not ok:
                # 未写入任何东西，无需还原
                print(f"[错误] {m.name}: {err}", file=sys.stderr)
                errored.append(m.name)
                continue

            restore_err = None
            try:
                rc, tail = _run_tests(m.tests)
            except Exception as e:
                rc, tail = -1, f"{type(e).__name__}: {e}"
            finally:
                # 无条件还原。还原本身的异常不在这里吞掉，而是带出去
                # 让外层明确报错——变异留在工作区是这个工具最严重的
                # 失效模式，绝不能静默继续。
                restore_err = _restore(target, backup, original_bytes)

            if restore_err:
                print(
                    f"\n[FATAL] {m.name} 无法还原 {m.target}！"
                    f"{restore_err}",
                    file=sys.stderr,
                )
                errored.append(m.name)
                continue

            if rc != 0:
                killed.append(m.name)
                print(f"[杀死] {m.name}  —— 测试按预期变红 ✓")
            else:
                survived.append(m.name)
                print(
                    f"[存活] {m.name}  —— 测试没抓到这个缺陷 ✗\n"
                    f"        说明这些测试有盲区: {m.tests}\n"
                    f"        请补强测试，或确认该 mutation 是否真的有害。",
                    file=sys.stderr,
                )
                print(f"        尾部输出:\n{tail}\n", file=sys.stderr)

            # 每个用例跑完立刻确认工作区干净
            if target.read_bytes() == original_bytes:
                print(f"        已还原 {m.target} ✓")
            print("-" * 72)

        print("=" * 72)
        print(f"结果：杀死 {len(killed)} / 存活 {len(survived)} / 错误 {len(errored)}")
        if survived:
            print(f"存活（需补强测试）: {survived}", file=sys.stderr)
        if errored:
            print(f"执行错误: {errored}", file=sys.stderr)

        return 0 if (not survived and not errored) else 1

    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
