"""子 agent-技能管理路由（多 agent 化后取代"医生-技能"路由 → 统一 agent 命名）

说明：
- /api/v1/agents 提供子 agent 列表 + 按子 agent 启停技能。
- 子 agent 即多 agent 里的 agent（patient_knowledge_agent / search_agent / ...），
  agents 表按 Agent.name 定位其 agent_id，agent_skills 表存启停。
- 旧的"医生实体管理"路由已移除（本路径现托管子 agent 技能）。
"""

from fastapi import APIRouter, Query, HTTPException

from backend.services import SubAgentSkillManager
from backend.database.session_factory import get_session_context

router = APIRouter(prefix="/api/v1/agents", tags=["Agent Skills"])


@router.get("")
async def list_agents(
    user_id: str = Query(..., min_length=1),
):
    """列出该租户下所有子 agent（名 + 描述 + 启用技能数）"""
    try:
        with get_session_context() as db:
            manager = SubAgentSkillManager(db)
            agents = manager.list_agents(user_id=user_id)

        return {"data": agents, "total": len(agents)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_name}/skills")
async def get_agent_skills(
    agent_name: str,
    user_id: str = Query(..., min_length=1),
):
    """列出某子 agent 的全部技能及启停状态"""
    try:
        with get_session_context() as db:
            manager = SubAgentSkillManager(db)
            result = manager.get_agent_all_skills(user_id=user_id, agent_name=agent_name)

        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_name}/skills/enabled")
async def get_enabled_skills(
    agent_name: str,
    user_id: str = Query(..., min_length=1),
):
    """列出某子 agent 已启用的技能（供运行时 / 调试）"""
    try:
        with get_session_context() as db:
            manager = SubAgentSkillManager(db)
            enabled = manager.get_enabled_skills(user_id=user_id, agent_name=agent_name)

        return {
            "agent_name": agent_name,
            "enabled_skills": enabled,
            "total_enabled": len(enabled),
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_name}/skills/{skill_id}/enable")
async def enable_skill(
    agent_name: str,
    skill_id: str,
    user_id: str = Query(..., min_length=1),
):
    """启用某子 agent 的技能"""
    try:
        with get_session_context() as db:
            manager = SubAgentSkillManager(db)
            manager.enable_skill(user_id=user_id, agent_name=agent_name, skill_id=skill_id)

        return {"message": "Skill enabled successfully", "agent_name": agent_name, "skill_id": skill_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_name}/skills/{skill_id}/disable")
async def disable_skill(
    agent_name: str,
    skill_id: str,
    user_id: str = Query(..., min_length=1),
):
    """禁用某子 agent 的技能"""
    try:
        with get_session_context() as db:
            manager = SubAgentSkillManager(db)
            manager.disable_skill(user_id=user_id, agent_name=agent_name, skill_id=skill_id)

        return {"message": "Skill disabled successfully", "agent_name": agent_name, "skill_id": skill_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
