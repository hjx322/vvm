#agent表
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, DateTime, Index
from .base import Base


class Agent(Base):
    """医生/Agent 模型"""

    __tablename__ = "agents"

    agent_id = Column(String(50), primary_key=True)  # Format: agt_{uuid.hex[:16]}
    name = Column(String(100), nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)


    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Agent(agent_id={self.agent_id}, agent_name={self.name}, user_id={self.user_id})>"
