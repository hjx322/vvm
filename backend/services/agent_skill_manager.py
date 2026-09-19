
from typing import List, Dict, Any
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.models import AgentSkill, Skill, Agent


class AgentSkillManager:
    """Manages agent-skill relationships (enable/disable, list enabled)"""

    def __init__(self, db_session: Session):
        self.db = db_session

    def enable_skill(self, agent_id: str, skill_id: str, user_id: str) -> AgentSkill:
        """
        为某个智能体启用技能。
        逻辑：
        1. 查询 agent_skill 表是否已存在该条目
        2. 如果存在，直接将 is_enabled 设为 1
        3. 如果不存在，向 agent_skill 表插入新条目且 is_enabled=1
        """
        # 验证医生权限
        agent = self.db.query(Agent).filter(and_(Agent.agent_id == agent_id, Agent.user_id == user_id)).first()
        if not agent:
            raise PermissionError(f"User {user_id} does not have access to agent {agent_id}")

        # 验证技能存在且对该用户可用（ownership 或 builtin）
        from sqlalchemy import or_
        skill = self.db.query(Skill).filter(
                Skill.skill_id == skill_id,
                or_(Skill.user_id == user_id, Skill.is_builtin == True)
            ).first()
        if not skill:
            raise ValueError(f"Skill {skill_id} not found")

        # 查询 agent_skill 是否已存在
        agent_skill = self.db.query(AgentSkill).filter(
            and_(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()

        if not agent_skill:
            # 不存在则插入新条目，且 is_enabled=True
            agent_skill = AgentSkill(agent_id=agent_id, skill_id=skill_id, is_enabled=True,created_at=datetime.utcnow(),updated_at = datetime.utcnow())
            self.db.add(agent_skill)
        else:
            # 存在则更新 is_enabled=True
            agent_skill.is_enabled = True
            agent_skill.updated_at = datetime.utcnow()

        self.db.commit()
        return agent_skill

    def disable_skill(self, agent_id: str, skill_id: str, user_id: str) -> AgentSkill:
        """
        为某个智能体禁用技能。
        逻辑：
        1. 查询 agent_skill 表中该条目
        2. 将 is_enabled 设为 0（禁用）
        """
        agent = self.db.query(Agent).filter(and_(Agent.agent_id == agent_id, Agent.user_id == user_id)).first()
        if not agent:
            raise PermissionError(f"User {user_id} does not have access to agent {agent_id}")

        agent_skill = self.db.query(AgentSkill).filter(
            and_(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()

        if not agent_skill:
            raise ValueError(f"Skill {skill_id} is not associated with agent {agent_id}")

        if agent_skill.is_enabled:
            agent_skill.is_enabled = False
            agent_skill.disabled_at = datetime.utcnow()

        self.db.commit()
        return agent_skill

    def get_enabled_skills(self, agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体已启用的技能（含租户隔离校验）

        只返回该智能体所属用户的自定义技能 + 系统内置技能。
        防止租户A启用租户B的技能后越权使用。
        """
        from sqlalchemy import or_

        # 先查出智能体所属的 user_id
        agent = self.db.query(Agent).filter(Agent.agent_id == agent_id).first()
        if not agent:
            return []

        owner_user_id = agent.user_id

        # JOIN 查询：只返回 owner 的技能 + 内置技能
        agent_skills = (
            self.db.query(AgentSkill, Skill)
            .join(Skill, AgentSkill.skill_id == Skill.skill_id)
            .filter(
                AgentSkill.agent_id == agent_id,
                AgentSkill.is_enabled == True,
                or_(Skill.user_id == owner_user_id, Skill.is_builtin == True),
            )
            .all()
        )

        enabled_skills = []
        for agent_skill, skill in agent_skills:
            enabled_skills.append({
                "skill_id": skill.skill_id,
                "description": skill.description,
                "language": skill.language,
                "file_path": skill.current_path,
            })

        return enabled_skills

    def get_agent_all_skills(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        """
        查询智能体的所有技能。
        逻辑：
        1. 在 agents 表查询用户 id
        2. 利用用户 id 在 skills 表查询用户的所有技能（内置 + 自定义）
        3. 返回所有技能及其启用状态
        """
        from sqlalchemy import or_

        # 验证医生权限
        agent = self.db.query(Agent).filter(and_(Agent.agent_id == agent_id, Agent.user_id == user_id)).first()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found for user {user_id}")

        # 获取用户的所有技能（内置 + 自定义）
        all_user_skills = self.db.query(Skill).filter(
            or_(Skill.user_id == user_id, Skill.is_builtin == True)
        ).all()

        skills_info = []
        for skill in all_user_skills:
            # 查询该技能是否为此医生启用
            agent_skill = self.db.query(AgentSkill).filter(
                and_(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill.skill_id)
            ).first()

            skill_info = {
                "skill_id": skill.skill_id,
                "description": skill.description,
                "language": skill.language,
                "is_enabled": agent_skill.is_enabled if agent_skill else False,
                "enabled_at": agent_skill.enabled_at.isoformat() if agent_skill and agent_skill.is_enabled else None,
            }
            skills_info.append(skill_info)

        return {
            "agent_id": agent_id,
            "user_id": user_id,
            "total_skills": len(all_user_skills),
            "skills": skills_info,
        }

    def is_skill_enabled(self, agent_id: str, skill_id: str, user_id: str) -> bool:
        """
        检查某个技能是否为医生启用。
        逻辑：
        1. 在 agent_skill 查询是否有该条目
        2. 查询 is_enabled 是否为 1（True）
        """
        # 验证医生权限
        agent = self.db.query(Agent).filter(and_(Agent.agent_id == agent_id, Agent.user_id == user_id)).first()
        if not agent:
            raise PermissionError(f"User {user_id} does not have access to agent {agent_id}")

        agent_skill = self.db.query(AgentSkill).filter(
            and_(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()

        return agent_skill.is_enabled if agent_skill else False



