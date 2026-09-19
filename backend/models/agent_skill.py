#
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, PrimaryKeyConstraint
from .base import Base


class AgentSkill(Base):
    """医生-技能关联模型"""

    __tablename__ = "agent_skills"

    agent_id = Column(String(50), ForeignKey("agents.agent_id"), nullable=False, primary_key=True, index=True)
    skill_id = Column(String(50), ForeignKey("skills.skill_id"), nullable=False, primary_key=True, index=True)

    # 技能是否启用
    is_enabled = Column(Boolean, default=False, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


    def __repr__(self):
        return f"<AgentSkill(agent_id={self.agent_id}, skill_id={self.skill_id}, is_enabled={self.is_enabled})>"
