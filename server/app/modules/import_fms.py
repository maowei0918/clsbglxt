"""数据导入通道（占位，FMS 还在运行，先不实现）。

未来从 fms 的 API 拉数据写进 clglxt：
  * fms 的 openapi 端点（/api/v2/vehicles、/repairs 等，需 API Key）
  * fms 的登录接口（拿 Bearer token，走内部端点）
  * 分批拉取 → 写入 clglxt 数据库

触发方式：后台任务（Celery），支持断点续传与幂等重试。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..core.deps import get_current_user

log = logging.getLogger("clglxt.import")

router = APIRouter(prefix="/import-fms", tags=["import"])

# ─────────────────────────── 状态占位 ───────────────────────────

# 导入任务运行时状态（Redis 里存进度），实现时填充：
#   clglxt:import:status  → {"running": bool, "total": int, "done": int,
#                            "current_step": str, "errors": [...]}
IMPORT_STATUS_KEY = "clglxt:import:status"


@router.get("/status", dependencies=[Depends(get_current_user)])
def get_import_status():
    """查询导入任务进度。"""
    return {"status": "not_implemented",
            "note": "FMS 仍在运行，导入功能预留接口，尚未实现"}


@router.post("/start", dependencies=[Depends(get_current_user)])
def start_import():
    """启动导入（预留）。"""
    raise HTTPException(501, "导入功能预留，待 FMS 停机前实施")


@router.post("/stop", dependencies=[Depends(get_current_user)])
def stop_import():
    """停止导入（预留）。"""
    raise HTTPException(501, "导入功能预留，待 FMS 停机前实施")
