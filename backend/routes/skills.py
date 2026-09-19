"""技能管理路由：上传 / 列表 / 删除

说明：
- POST /upload 接收 zip 技能包（需含带 name 字段 frontmatter 的 SKILL.md），
  由 SkillManager.upload_skill 落盘到 user_skills/{user_id}/{skill_id}/current/
  并在 skills 表登记，同时为该用户所有医生创建 agent_skills 关联（默认禁用）。
"""
import os
import tempfile

from fastapi import APIRouter, Query, HTTPException, UploadFile, File

from backend.services import SkillManager
from backend.database.session_factory import get_session_context

router = APIRouter(prefix="/api/v1/skills", tags=["Skills"])


@router.post("/upload")
async def upload_skill(
    file: UploadFile = File(...),
    user_id: str = Query(..., min_length=1),
):
    """上传技能 ZIP 包并注册到技能表"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 技能包")

    temp_zip = None
    try:
        # 落到临时 zip 文件，供 FileManager 解压解析
        fd, temp_zip = tempfile.mkstemp(suffix=".zip")
        with os.fdopen(fd, "wb") as f:
            f.write(content)

        with get_session_context() as db:
            manager = SkillManager(db)
            skill = manager.upload_skill(user_id=user_id, zip_file_path=temp_zip)

        return {
            "skill_id": skill.skill_id,
            "description": skill.description,
            "language": skill.language,
            "current_path": skill.current_path,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_zip and os.path.exists(temp_zip):
            os.remove(temp_zip)


@router.get("")
async def list_skills(
    user_id: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """列出某用户可见的技能（自定义 + 内置）"""
    try:
        with get_session_context() as db:
            manager = SkillManager(db)
            result = manager.list_user_skills(
                user_id=user_id, page=page, page_size=page_size
            )

        return {
            "data": [
                {
                    "skill_id": s.skill_id,
                    "description": s.description,
                    "language": s.language,
                    "is_builtin": s.is_builtin,
                    "current_path": s.current_path,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in result["data"]
            ],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
            "pages": result["pages"],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    user_id: str = Query(..., min_length=1),
):
    """删除一个自定义技能（内置技能不允许删除）"""
    try:
        with get_session_context() as db:
            manager = SkillManager(db)
            manager.delete_skill(user_id=user_id, skill_id=skill_id)

        return {"message": "Skill deleted successfully", "skill_id": skill_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))