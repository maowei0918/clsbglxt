"""pytest 收集配置。

历史遗留的脚本式测试（模块顶层直接执行、末尾 sys.exit）不能被 pytest
作为模块导入，否则收集阶段就会触发 SystemExit / INTERNALERROR。
这里把它们排除出直接收集，改由 test_scripts_regression.py 以子进程方式
隔离执行，从而让 `pytest tests/` 成为统一入口。
"""

collect_ignore = [
    "test_e2e.py",
    "test_install_security.py",
    "test_photo_pipeline.py",
    "test_softdelete.py",
]
