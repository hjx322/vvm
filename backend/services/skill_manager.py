#技能上传/更新
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.models import Skill
from backend.utils.file_manager import FileManager


class SkillManager:
    """Manages skill lifecycle (upload, delete, list, get details)"""

    def __init__(self, db_session: Session, file_manager: FileManager = None):
        self.db = db_session
        self.file_manager = file_manager or FileManager()

    def upload_skill(
        self,
        user_id: str,
        zip_file_path: str,
        skill_id: str = None,
        description: str = None,
        language: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Skill:
        """
        上传或更新技能。元数据优先级：
        1. SKILL.md 中的frontmatter值（最优先）
        2. 用户显式传入的参数（覆盖SKILL.md）
        3. 默认值（language='python3'）

        分支：
        - 全新上传：向 skills 表插入记录，并为该用户的所有医生在 agent_skills 表中创建映射
        - 技能更新：更新 skills 表，保持现有的 agent_skills 映射不变
        """
        # 第一步：解析SKILL.md
        skill_meta = self.file_manager.parse_skill_metadata(zip_file_path)

        # 第二步：合并参数（用户显式参数覆盖SKILL.md）
        final_skill_id = skill_id or skill_meta.get("skill_id")
        final_description = description or skill_meta.get("description")
        final_language = language or skill_meta.get("language", "python3")
        final_content = skill_meta.get("content")  # 总是使用SKILL.md内容

        if not final_skill_id:
            raise ValueError("skill_id 不能为空，请在SKILL.md的name字段中指定")

        # 第三步：检查技能是否已存在（同一用户、同一skill_id）
        existing_skill = self.db.query(Skill).filter(
            and_(
                Skill.user_id == user_id,
                Skill.skill_id == final_skill_id,
                Skill.is_builtin == False,
            )
        ).first()

        # 第四步：解压ZIP并保存文件
        temp_dir = self.file_manager.extract_zip_to_temp(zip_file_path, final_skill_id)
        current_path = self.file_manager.save_skill_version(user_id, final_skill_id, temp_dir)

        # 第五步：保存到数据库
        is_new_skill = existing_skill is None
        if existing_skill:
            # 分支 B：技能更新 - 只更新 skills 表，不修改 agent_skills 映射
            existing_skill.description = final_description
            existing_skill.language = final_language
            existing_skill.content = final_content
            existing_skill.current_path = current_path
            existing_skill.updated_at = datetime.utcnow()
            self.db.commit()
            skill = existing_skill
        else:
            # 分支 A：全新上传 - 插入 skills 表，并为该用户所有医生在 agent_skills 表中创建映射（默认禁用，由医生端 enable 开启）
            skill = Skill(
                skill_id=final_skill_id,
                description=final_description,
                language=final_language,
                content=final_content,
                user_id=user_id,
                is_builtin=False,
                current_path=current_path,
            )
            self.db.add(skill)
            self.db.commit()

            # 为该用户所有医生创建技能关联（is_enabled=False），与顶部注释对齐
            from backend.models import Agent, AgentSkill
            user_agents = self.db.query(Agent).filter(Agent.user_id == user_id).all()
            for agent in user_agents:
                exist = self.db.query(AgentSkill).filter(
                    and_(
                        AgentSkill.agent_id == agent.agent_id,
                        AgentSkill.skill_id == final_skill_id,
                    )
                ).first()
                if not exist:
                    self.db.add(
                        AgentSkill(
                            agent_id=agent.agent_id,
                            skill_id=final_skill_id,
                            is_enabled=False,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                    )
            self.db.commit()

        # 清理临时文件
        self.file_manager.cleanup_temp_files(final_skill_id)
        return skill

    def delete_skill(self, user_id: str, skill_id: str) -> bool:
        """Delete a skill (and all associated agent_skills). Cannot delete built-in skills."""
        skill = self.db.query(Skill).filter(
            and_(Skill.skill_id == skill_id, Skill.user_id == user_id)
        ).first()

        if not skill:
            raise ValueError(f"Skill {skill_id} not found for user {user_id}")


        if skill.is_builtin:
            raise PermissionError(f"Cannot delete built-in skill {skill_id}")

        # 根据 user_id 查询该用户的所有医生，然后删除这些医生与该技能的关联
        from backend.models import Agent, AgentSkill
        user_agents = self.db.query(Agent).filter(Agent.user_id == user_id).all()

        for agent in user_agents:
            # 查询该医生和该技能是否存在关联，存在就删除
            agent_skill = self.db.query(AgentSkill).filter(
                and_(AgentSkill.agent_id == agent.agent_id, AgentSkill.skill_id == skill_id)
            ).first()
            if agent_skill:
                self.db.delete(agent_skill)

        # Delete skill files
        self.file_manager.delete_skill_files(user_id, skill_id)

        # Delete skill
        self.db.delete(skill)
        self.db.commit()
        return True

    def list_user_skills(self, user_id: str, page: int = 1, page_size: int = 20, include_builtin: bool = True) -> Dict[str, Any]:
        """List all skills for a user (custom + built-in)"""
        query = self.db.query(Skill)

        if include_builtin:
            from sqlalchemy import or_
            query = query.filter(or_(Skill.user_id == user_id, Skill.is_builtin == True))
        else:
            query = query.filter(and_(Skill.user_id == user_id, Skill.is_builtin == False))

        total = query.count()
        offset = (page - 1) * page_size
        skills = query.offset(offset).limit(page_size).all()

        return {
            "data": skills,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }

    def get_skill_detail(self, user_id: str, skill_id: str) -> Skill:
        """获取技能详情（含租户权限校验）"""
        from sqlalchemy import or_

        skill = self.db.query(Skill).filter(
            Skill.skill_id == skill_id,
            or_(Skill.user_id == user_id, Skill.is_builtin == True)
        ).first()

        if not skill:
            raise PermissionError(
                f"Skill {skill_id} not found or user {user_id} does not have access"
            )

        return skill
