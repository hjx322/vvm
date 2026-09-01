"""Integration module for DigitalSmartDoctorAgent"""
from typing import List, Dict, Any
from backend.services import AgentSkillManager
from backend.database.session_factory import get_session_context


def get_skills_for_agent(agent_id: str) -> List[Dict[str, Any]]:
    """
    Get enabled skills for a specific agent (primary integration point).
    Called from DigitalSmartDoctorAgent to build LLM tool list.
    """
    try:
        with get_session_context() as db:
            manager = AgentSkillManager(db)
            return manager.get_enabled_skills(agent_id=agent_id)
    except Exception as e:
        print(f"❌ Error fetching skills for agent {agent_id}: {str(e)}")
        return []


def verify_agent_exists(agent_id: str) -> bool:
    """Verify if an agent exists"""
    try:
        from backend.models import Agent
        with get_session_context() as db:
            agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
            return agent is not None
    except Exception:
        return False


def verify_skill_enabled(agent_id: str, skill_id: str) -> bool:
    """Verify if a skill is enabled for a specific agent"""
    try:
        from backend.models import AgentSkill
        from sqlalchemy import and_
        with get_session_context() as db:
            agent_skill = db.query(AgentSkill).filter(
                and_(
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.skill_id == skill_id,
                    AgentSkill.is_enabled == True,
                )
            ).first()
            return agent_skill is not None
    except Exception:
        return False
