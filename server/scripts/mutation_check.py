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

            try:
                rc, tail = _run_tests(m.tests)
            finally:
                # 无条件还原
                target.write_bytes(original_bytes)
                now_bytes = target.read_bytes()
                if now_bytes != original_bytes:
                    # 还原失败：从备份再试一次，然后大声报错
                    shutil.copy2(backup, target)
                    if target.read_bytes() != original_bytes:
                        print(
                            f"\n[FATAL] 无法还原 {m.target}！"
                            f"请立即手动从 {backup} 恢复，"
                            f"或检查该文件是否被其它进程改写。",
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
