"""部门管理。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import require_perm
from ..models import Department

log = logging.getLogger("clglxt.departments")
router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", dependencies=[Depends(require_perm("system:department_manage"))])
def list_departments(db: Session = Depends(get_db)):
    depts = db.query(Department).order_by(Department.id).all()
    return [{"id": d.id, "name": d.name, "remark": d.remark} for d in depts]


@router.post("", dependencies=[Depends(require_perm("system:department_manage"))])
def create_department(name: str, remark: str = "", db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "部门名称不能为空")
    if db.query(Department).filter(Department.name == name).first():
        raise HTTPException(400, "该部门名称已存在")
    dept = Department(name=name, remark=remark)
    db.add(dept)
    db.commit()
    return {"id": dept.id, "name": dept.name}


@router.put("/{dept_id}", dependencies=[Depends(require_perm("system:department_manage"))])
def update_department(dept_id: int, name: str | None = None, remark: str | None = None,
                      db: Session = Depends(get_db)):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(404, "部门不存在")
    if name is not None:
        name = name.strip()
        if name and name != dept.name:
            if db.query(Department).filter(Department.name == name).first():
                raise HTTPException(400, "该部门名称已存在")
            dept.name = name
    if remark is not None:
        dept.remark = remark
    db.commit()
    return {"ok": True}


@router.delete("/{dept_id}", dependencies=[Depends(require_perm("system:department_manage"))])
def delete_department(dept_id: int, db: Session = Depends(get_db)):
    from ..models import User, Vehicle, TempVehicleUse
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(404, "部门不存在")

    # 引用检查
    if db.query(User).filter(User.department_id == dept_id).count() > 0:
        raise HTTPException(400, "该部门下仍有人员，无法删除")
    if db.query(Vehicle).filter(Vehicle.department_id == dept_id).count() > 0:
        raise HTTPException(400, "该部门下仍有车辆，无法删除")
    if db.query(TempVehicleUse).filter(TempVehicleUse.department_id == dept_id).count() > 0:
        raise HTTPException(400, "该部门下仍有临时用车记录，无法删除")

    db.delete(dept)
    db.commit()
    return {"ok": True}
