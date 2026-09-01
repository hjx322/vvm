#医生的创建
import uuid
import json
from typing import Dict, Any
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.models import Agent, AgentSkill, Skill


class AgentManager:
    """Manages agent/doctor lifecycle (create, delete, list, get details)"""

    def __init__(self, db_session: Session):
        self.db = db_session

    def create_agent(
        self,
        user_id: str,
        agent_name: str,
        description: str = None,
        specialization: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Agent:
        """Create a new agent/doctor."""
        agent_id = f"agt_{uuid.uuid4().hex[:16]}"

        agent = Agent(
            agent_id=agent_id,
            name=agent_name,
            user_id=user_id,
        )
        self.db.add(agent)
        self.db.commit()
        return agent

    def update_agent(
        self,
        user_id: str,
        agent_id: str,
        agent_name: str = None,
    ) -> Agent:
        """Update an agent's name.

        Args:
            user_id: 所属用户/租户 id，用于归属校验
            agent_id: 要修改的医生 id
            agent_name: 新的医生名称；为空则不修改

        Returns:
            Agent: 更新后的医生对象

        Raises:
            ValueError: 医生不存在或不属于该用户
        """
        agent = self.db.query(Agent).filter(
            and_(Agent.agent_id == agent_id, Agent.user_id == user_id)
        ).first()

        if not agent:
            raise ValueError(f"Agent {agent_id} not found for user {user_id}")

        if agent_name:
            agent.name = agent_name
        self.db.commit()
        return agent

    def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent (and all associated agent_skills)."""
        agent = self.db.query(Agent).filter(
            and_(Agent.agent_id == agent_id, Agent.user_id == user_id)
        ).first()

        if not agent:
            raise ValueError(f"Agent {agent_id} not found for user {user_id}")

        self.db.query(AgentSkill).filter(AgentSkill.agent_id == agent_id).delete()
        self.db.delete(agent)
        self.db.commit()
        return True

    def list_user_agents(self, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """List all agents for a user"""
        query = self.db.query(Agent).filter(Agent.user_id == user_id)
        total = query.count()
        offset = (page - 1) * page_size
        agents = query.offset(offset).limit(page_size).all()

        return {"data": agents, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}

    def get_agent_details(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        """Get detailed information about an agent including enabled/disabled skills"""
        agent = self.db.query(Agent).filter(and_(Agent.agent_id == agent_id, Agent.user_id == user_id)).first()

        if not agent:
            raise ValueError(f"Agent {agent_id} not found for user {user_id}")

        # 获取用户的所有技能（内置 + 自定义）
        from sqlalchemy import or_
        all_user_skills = self.db.query(Skill).filter(
            or_(Skill.user_id == user_id, Skill.is_builtin == True)
        ).all()

        enabled_skills = []
        disabled_skills = []

        for skill in all_user_skills:
            # 查询该技能是否为此医生启用
            agent_skill = self.db.query(AgentSkill).filter(
                and_(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill.skill_id)
            ).first()

            skill_info = {
                "skill_id": skill.skill_id,
                "description": skill.description,
                "language": skill.language,
            }

            if agent_skill and agent_skill.is_enabled:
                enabled_skills.append(skill_info)
            else:
                disabled_skills.append(skill_info)

        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "user_id": agent.user_id,
            "created_at": agent.created_at.isoformat(),
            "enabled_skills": enabled_skills,
            "disabled_skills": disabled_skills,
            "total_skills": len(all_user_skills),
            "enabled_count": len(enabled_skills),
        }
