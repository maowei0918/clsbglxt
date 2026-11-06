"""数据导入通道：从老 FMS 拉数据写入 clglxt。

数据源：FMS v2 对外只读 API `/api/v2/open/*`（Bearer fms_xxx，scope 鉴权）。
触发方式：Celery 异步任务，支持断点续传与幂等重试。

迁移口径（见 docs/deploy-plan.md）：
  * 通过老 FMS API 拉取导入，不走双写
  * FMS 停机前实施

能力边界（重要）：
  开放接口给的是**业务摘要**（车牌/单号/状态/金额/日期），不含报价明细、
  审批动作流、附件实体。因此授权范围是「主数据 + 单据摘要」。
  完整历史明细需停机后直读 fms_v2.db 或走 FMS 内部接口，不在本通道范围。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import fms_client, fms_importer
from ..core.deps import get_current_user, require_perm

log = logging.getLogger("clglxt.import")

router = APIRouter(prefix="/import-fms", tags=["import"])


class StartBody(BaseModel):
    scopes: list[str] | None = Field(default=None, description="要导入的数据域，缺省全部")
    batch_size: int = Field(default=100, ge=10, le=1000, description="每批提交条数")
    sync: bool = Field(default=False, description="同步执行（仅调试/小数据量用）")


@router.get("/config")
def get_import_config(_: object = Depends(require_perm("system:setting_backup"))):
    """查看当前 FMS 数据源配置（不回显密钥明文，只给是否已配置）。"""
    from ..core.config import FMS_BASE_URL, FMS_PAGE_SIZE

    return {
        "base_url": FMS_BASE_URL or None,
        "api_key_configured": bool(fms_client.FMS_API_KEY),
        "page_size": FMS_PAGE_SIZE,
        "allowed_hosts": [h for h in fms_client.SSRF_ALLOWED_HOSTS if h],
        "available_scopes": fms_client.available_scopes(),
        "import_order": fms_importer.SCOPE_ORDER,
    }


@router.get("/status")
def get_import_status(_: object = Depends(get_current_user)):
    """查询导入任务进度（含已处理的最近错误）。"""
    return fms_importer.get_status()


@router.post("/test")
def test_connection(_: object = Depends(require_perm("system:setting_backup"))):
    """测试与老 FMS 的连通性（配置 + 网络 + 密钥 + scope）。"""
    result = fms_client.test_connection()
    if not result.get("ok"):
        # 连接失败不算服务端错误，返回 200 + ok=false 便于前端展示诊断
        log.warning("FMS 连接测试失败：%s", result.get("error"))
    return result


@router.post("/start")
def start_import(body: StartBody, _: object = Depends(require_perm("system:setting_backup"))):
    """启动导入。

    默认投递到 Celery 异步执行；`sync=true` 时同步跑（仅建议小数据量调试）。
    """
    st = fms_importer.get_status()
    if st.get("running"):
        raise HTTPException(409, "已有导入任务在运行，请先停止或等待完成")

    if body.sync:
        try:
            return fms_importer.run_import(scopes=body.scopes, batch_size=body.batch_size)
        except fms_client.FmsConfigError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.exception("同步导入失败")
            raise HTTPException(500, f"导入失败：{e}") from e

    fms_importer.reset_progress(keep_map=True)
    try:
        from ..workers.import_tasks import run_import_job
        run_import_job.delay(body.scopes, body.batch_size)
    except Exception as e:  # noqa: BLE001
        log.exception("投递导入任务失败")
        raise HTTPException(503, f"无法投递导入任务（Celery 不可用？）：{e}") from e

    return {"started": True, "scopes": body.scopes or fms_importer.SCOPE_ORDER}


@router.post("/stop")
def stop_import(_: object = Depends(require_perm("system:setting_backup"))):
    """请求停止导入（协作式中止：任务在下一个批次边界退出）。"""
    st = fms_importer.get_status()
    if not st.get("running"):
        return {"stopped": False, "note": "当前没有正在运行的导入任务"}
    fms_importer.request_cancel()
    return {"stopped": True, "note": "已发送停止信号，将在当前批次结束后退出"}


@router.post("/reset")
def reset_import(keep_map: bool = True,
                 _: object = Depends(require_perm("system:setting_backup"))):
    """清空导入进度。keep_map=true 时保留 ID 映射（默认，避免重复导入丢外键）。"""
    fms_importer.reset_progress(keep_map=keep_map)
    return {"reset": True, "kept_id_map": keep_map}