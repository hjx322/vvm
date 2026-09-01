from fastapi import APIRouter, Query, HTTPException

from backend.services import AgentSkillManager
from backend.database.session_factory import get_session_context

router = APIRouter(prefix="/api/v1/agents", tags=["Agent Skills"])


@router.post("/{agent_id}/skills/{skill_id}/enable")
async def enable_skill(
    agent_id: str,
    skill_id: str,
    user_id: str = Query(..., min_length=1),
):
    """Enable a skill for an agent"""
    try:
        with get_session_context() as db:
            manager = AgentSkillManager(db)
            agent_skill = manager.enable_skill(
                agent_id=agent_id,
                skill_id=skill_id,
                user_id=user_id,
            )

        return {
            "message": "Skill enabled successfully",
            "agent_id": agent_id,
            "skill_id": skill_id,
            "is_enabled": agent_skill.is_enabled,
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/skills/{skill_id}/disable")
async def disable_skill(
    agent_id: str,
    skill_id: str,
    user_id: str = Query(..., min_length=1),
):
    """Disable a skill for an agent"""
    try:
        with get_session_context() as db:
            manager = AgentSkillManager(db)
            agent_skill = manager.disable_skill(
                agent_id=agent_id,
                skill_id=skill_id,
                user_id=user_id,
            )

        return {
            "message": "Skill disabled successfully",
            "agent_id": agent_id,
            "skill_id": skill_id,
            "is_enabled": agent_skill.is_enabled,
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/skills/enabled")
async def get_enabled_skills(
    agent_id: str,
    user_id: str = Query(..., min_length=1),
):
    """Get all enabled skills for an agent (used by LLM)"""
    try:
        with get_session_context() as db:
            manager = AgentSkillManager(db)
            enabled_skills = manager.get_enabled_skills(agent_id=agent_id)

        return {
            "agent_id": agent_id,
            "enabled_skills": enabled_skills,
            "total_enabled": len(enabled_skills),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
