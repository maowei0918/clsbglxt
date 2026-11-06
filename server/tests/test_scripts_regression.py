#!/usr/bin/env python3
"""把脚本式回归测试纳入 pytest 统一入口，并校验它们不污染工作区。

历史原因：test_e2e.py / test_install_security.py / test_photo_pipeline.py /
test_softdelete.py 都是「模块顶层直接执行 + 末尾 sys.exit」的脚本，不能被
pytest 直接收集（收集阶段就 SystemExit）。本文件用 subprocess 隔离执行它们，
让 `pytest tests/` 能一次跑完全部回归。

隔离要点：
  * 子进程用当前解释器（venv）运行，但清掉 PYTHONPATH（外部 site-packages 污染）
  * 清掉所有 CLGLXT_* 环境变量，避免 pytest 进程内被其它测试污染的 env 串味
  * 每个脚本自己设置所需变量

工作区守护（本文件的核心价值）：
  这些脚本会调用真实的 /api/install/save。installer 曾把 ENV_FILE 等做成
  模块级常量，由 BASE_DIR 推导、不受 CLGLXT_DATA_DIR 影响，测试靠事后逐个
  重绑 inst.XXX 隔离——极易漏，历史上 test_e2e.py / test_photo_pipeline.py
  因此改写生产 server/.env 的 JWT_SECRET 与站点名（JWT_SECRET 轮换会让
  所有在线 token 失效）。现改为 installer.configure() 官方注入入口：
  一次调用同步全部路径（含三个派生安装标记），新增路径登记一次即生效。

  三层校验：
    1. 每个脚本执行前后比对工作区快照，定位到具体是哪个脚本越界
    2. 模块级基准快照在全部脚本跑完后再比对一次，防止多脚本累积掩盖
    3. 契约测试：脚本必须调用 configure()，且不允许再直接赋值路径常量
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent

SCRIPTS = [
    "test_softdelete.py",
    "test_install_security.py",
    "test_photo_pipeline.py",
    "test_e2e.py",
]

# 必须保持原样的工作区文件（相对 SERVER_DIR）
GUARDED_FILES = [".env"]

# 不允许出现的安装期残留（相对 SERVER_DIR）
FORBIDDEN_ARTIFACTS = [
    "data/.install_done",
    "data/.install.running",
    "data/.install_rl.json",
    ".env.tmp",
]


def _clean_env(tmp_dir) -> dict:
    env = os.environ.copy()
    # 去掉外部 site-packages 劫持（pydantic_core 二进制损坏）
    env.pop("PYTHONPATH", None)
    # 去掉 CLGLXT_* ，让每个脚本在干净环境下自建
    for k in list(env):
        if k.startswith("CLGLXT_"):
            env.pop(k, None)
    # 统一指定临时数据目录：避免脚本残留 .install_done / uploads 污染 server/data
    env["CLGLXT_DATA_DIR"] = str(tmp_dir)
    return env


def _snapshot() -> dict:
    """记录受守护文件的内容摘要；文件不存在记为 None。"""
    snap = {}
    for rel in GUARDED_FILES:
        p = SERVER_DIR / rel
        snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return snap


@pytest.fixture(scope="module", autouse=True)
def _workspace_baseline():
    """记录全模块基准，跑完所有脚本后再校验一次。"""
    baseline = _snapshot()
    yield baseline
    after = _snapshot()
    drifted = {k: (baseline[k], after[k]) for k in baseline if baseline[k] != after[k]}
    assert not drifted, (
        "脚本式测试整体跑完后工作区被污染（前面单个用例的 before/after 可能被"
        f"累积掩盖）: {drifted}"
    )
    leftover = [rel for rel in FORBIDDEN_ARTIFACTS if (SERVER_DIR / rel).exists()]
    assert not leftover, f"安装期残留未清理: {leftover}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_legacy_script(script, tmp_path):
    path = HERE / script
    assert path.exists(), f"脚本不存在: {path}"

    before = _snapshot()

    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(SERVER_DIR),
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    stdout = (proc.stdout or "")[-2000:]
    stderr = (proc.stderr or "")[-2000:]
    assert proc.returncode == 0, (
        f"{script} 失败（rc={proc.returncode}）\n"
        f"--- stdout tail ---\n{stdout}\n"
        f"--- stderr tail ---\n{stderr}"
    )

    after = _snapshot()
    drifted = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not drifted, (
        f"{script} 污染了工作区受守护文件: {drifted}\n"
        "提示：installer 路径隔离必须走 inst.configure(data_dir=..., env_file=...)，"
        "不要直接赋值 inst.ENV_FILE 等（ENV_FILE 由 BASE_DIR 推导，不受 CLGLXT_DATA_DIR 影响）。"
    )


# ───────────── 契约守护：隔离方式本身 ─────────────

# 禁止脚本直接赋值的路径属性（必须走 configure()）
_FORBIDDEN_ASSIGN = [
    "DATA_DIR", "INSTALL_LOCK", "INSTALL_RUNNING", "INSTALL_RL", "ENV_FILE",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_uses_configure_injection(script):
    """脚本必须用 configure() 注入路径，且不得再直接赋值路径常量。

    直接赋值（inst.ENV_FILE = ...）会漏掉 configure() 里新增的派生路径，
    这正是当初 test_e2e.py / test_photo_pipeline.py 改写生产 .env 的根因。
    """
    src = (HERE / script).read_text(encoding="utf-8")

    assigned = [
        name for name in _FORBIDDEN_ASSIGN
        if re.search(r'inst\.%s\s*=' % name, src)
        or re.search(r'installer\.%s\s*=' % name, src)
    ]
    assert not assigned, (
        f"{script} 直接赋值了路径常量 {assigned}，应改用 inst.configure(...)。"
        "直接赋值无法覆盖新增的派生路径，会导致隔离失效。"
    )

    # 走安装流程的脚本（调用 /api/install/save）必须调用 configure()
    if "/api/install/save" in src or "install/save" in src:
        assert "configure(" in src, (
            f"{script} 调用安装流程但未使用 inst.configure()，"
            "ENV_FILE 会落到生产 server/.env。"
        )


def test_configure_covers_all_paths():
    """静态契约：_paths 里每个 key 都必须在 configure() 内被赋值。

    防止「新增一个路径常量却忘了让它可注入」，从而重新引入隔离漏洞。
    """
    import inspect
    import app.installer as inst

    src = inspect.getsource(inst)
    # 提取 _paths 字典字面量的 key
    m = re.search(r'_paths\s*:\s*dict\s*=\s*\{(.*?)\}', src, re.S)
    assert m, "找不到 _paths 定义"
    keys = re.findall(r'["\'](\w+)["\']\s*:', m.group(1))
    assert keys, "_paths 未解析出任何 key"

    # 提取 configure() 函数体
    cm = re.search(r'def configure\(.*?\n(.*?)(?=\ndef |\nclass )', src, re.S)
    assert cm, "找不到 configure() 定义"
    body = cm.group(1)

    missing = [k for k in keys if f'_paths["{k}"]' not in body]
    assert not missing, (
        f"以下路径未在 configure() 内赋值，无法被注入: {missing}。"
        "新增路径时必须同步加到 configure()，否则测试隔离会失效。"
    )
