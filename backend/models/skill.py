from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, DateTime, Index, PrimaryKeyConstraint
from .base import Base


class Skill(Base):
    """技能模型"""

    __tablename__ = "skills"

    skill_id = Column(String(50), nullable=False, primary_key=True)
    user_id = Column(String(50), nullable=False, primary_key=True)  # 复合主键的一部分
    description = Column(Text, nullable=True)
    is_builtin = Column(Boolean, default=False, index=True)

    # 脚本语言
    language = Column(String(32), default='python3')

    # 文件路径（并没有用）
    current_path = Column(String(500), nullable=True)  # Current version path

    # SKILL.md content
    content = Column(Text, nullable=True)  # Full SKILL.md content


    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)



    def __repr__(self):
        return f"<Skill(skill_id={self.skill_id}, user_id={self.user_id})>"
