from fastapi import APIRouter, Query, HTTPException
from typing import Optional

from backend.services import AgentManager
from backend.database.session_factory import get_session_context

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"])


@router.post("")
async def create_agent(
    agent_name: str = Query(..., min_length=1),
    user_id: str = Query(..., min_length=1),
    description: Optional[str] = Query(None),
    specialization: Optional[str] = Query(None),
):
    """Create a new agent/doctor"""
    try:
        with get_session_context() as db:
            manager = AgentManager(db)
            agent = manager.create_agent(
                user_id=user_id,
                agent_name=agent_name,
                description=description,
                specialization=specialization,
            )

        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "user_id": agent.user_id,
            "created_at": agent.created_at.isoformat(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_agents(
    user_id: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List all agents for a user"""
    try:
        with get_session_context() as db:
            manager = AgentManager(db)
            result = manager.list_user_agents(user_id=user_id, page=page, page_size=page_size)

        return {
            "data": [{"agent_id": a.agent_id, "agent_name": a.name} for a in result["data"]],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
            "pages": result["pages"],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_agent_details(
    agent_id: str,
    user_id: str = Query(..., min_length=1),
):
    """Get detailed information about an agent"""
    try:
        with get_session_context() as db:
            manager = AgentManager(db)
            result = manager.get_agent_details(user_id=user_id, agent_id=agent_id)

        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    user_id: str = Query(..., min_length=1),
    agent_name: str = Query(None, min_length=1),
):
    """Update an agent/doctor's name"""
    try:
        with get_session_context() as db:
            manager = AgentManager(db)
            agent = manager.update_agent(
                user_id=user_id,
                agent_id=agent_id,
                agent_name=agent_name,
            )

        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "user_id": agent.user_id,
            "created_at": agent.created_at.isoformat(),
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, user_id: str = Query(..., min_length=1)):
    """Delete an agent"""
    try:
        with get_session_context() as db:
            manager = AgentManager(db)
            manager.delete_agent(user_id=user_id, agent_id=agent_id)

        return {"message": "Agent deleted successfully", "agent_id": agent_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
