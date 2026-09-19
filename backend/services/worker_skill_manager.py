# -*- coding: utf-8 -*-
"""子 agent-技能管理服务（多 agent 化后取代"医生-技能"管理 → 统一 agent 命名）

背景：多 agent 化后，技能配置从"医生(agent)维度"迁移到"子 agent 维度"。
agents 表语义 = 子 agent 注册表（name 列存子 agent 名），agent_skills 表语义 =
子 agent-技能绑定（is_enabled 控制启停）。本类按 Agent.name == agent_name 定位。

子 agent 的中文描述来自静态映射（非 DB），与 agent/multi_agent/workers.py +
reasoning_workers.py 的 description 保持一致。
"""

from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from backend.models import Agent, AgentSkill, Skill


# 子 agent 名 -> 中文描述（与 agent 类 description 一致；运行时以代码为准，此处仅供管理 API 展示）
AGENT_DESCRIPTIONS: Dict[str, str] = {
    "patient_knowledge_agent": "查询患者档案/就诊记录/检查结果 + 检索疾病/药物/护理的医学知识库",
    "search_agent": "互联网联网检索最新信息",
    "imaging_agent": "皮肤图像识别（用户上传皮肤照片/描述皮肤病症状时）",
    "safety_agent": "用药合规门卫：复核回答是否出现药名/剂量/开药表述",
    "general_agent": "通用助手：寒暄/常识/资讯/娱乐等非医疗或混合问题的承接",
}

# 已知子 agent 名集合（用于过滤 agents 表中的混淆行，避免把旧医生行当成子 agent）
KNOWN_AGENTS: tuple = tuple(AGENT_DESCRIPTIONS.keys())


class SubAgentSkillManager:
    """子 agent-技能生命周期管理（list / enable / disable / enabled）"""

    def __init__(self, db_session: Session):
        self.db = db_session

    # ---- 定位辅助 ----

    def _get_agent(self, user_id: str, agent_name: str) -> Agent:
        """按子 agent 名反查 agents 行（租户校验）。"""
        agent = (
            self.db.query(Agent)
            .filter(and_(Agent.name == agent_name, Agent.user_id == user_id))
            .first()
        )
        if not agent:
            raise ValueError(f"子 agent {agent_name} not found for user {user_id}")
        return agent

    # ---- 查询 ----

    def list_agents(self, user_id: str) -> List[Dict]:
        """列出该租户下所有已知子 agent（名 + 描述 + 启用技能数）。"""
        result = []
        for agent_name in KNOWN_AGENTS:
            agent = (
                self.db.query(Agent)
                .filter(and_(Agent.name == agent_name, Agent.user_id == user_id))
                .first()
            )
            if not agent:
                # 允许种子脚本未跑时前端也能看到子 agent（描述仍展示，技能数为 0）
                agent_id = None
                enabled = 0
            else:
                agent_id = agent.agent_id
                # 复用 get_enabled_skills（运行时同一口径）：只算该租户可见的技能，
                # 保证列表头部的数字 == 展开明细里"已启用"的行数 == 子 agent 实际拿到的技能数
                enabled = len(self.get_enabled_skills(user_id, agent_name))
            result.append(
                {
                    "agent_name": agent_name,
                    "description": AGENT_DESCRIPTIONS.get(agent_name, ""),
                    "agent_id": agent_id,
                    "enabled_count": enabled,
                }
            )
        return result

    def get_agent_all_skills(self, user_id: str, agent_name: str) -> Dict:
        """该子 agent 的全部技能及启停状态（owner 技能 + 内置）。"""
        agent = self._get_agent(user_id, agent_name)

        # 该租户可见的全部技能（内置 + 自定义），与 skill_manager.list_user_skills 同口径
        all_user_skills = (
            self.db.query(Skill)
            .filter(or_(Skill.user_id == user_id, Skill.is_builtin == True))  # noqa: E712
            .order_by(Skill.skill_id)
            .all()
        )

        skills_info = []
        for skill in all_user_skills:
            agent_skill = (
                self.db.query(AgentSkill)
                .filter(
                    and_(
                        AgentSkill.agent_id == agent.agent_id,
                        AgentSkill.skill_id == skill.skill_id,
                    )
                )
                .first()
            )
            skills_info.append(
                {
                    "skill_id": skill.skill_id,
                    "description": skill.description,
                    "language": skill.language,
                    "is_builtin": bool(skill.is_builtin),
                    "is_enabled": bool(agent_skill.is_enabled) if agent_skill else False,
                }
            )

        return {
            "agent_name": agent_name,
            "description": AGENT_DESCRIPTIONS.get(agent_name, ""),
            "agent_id": agent.agent_id,
            "total_skills": len(all_user_skills),
            "enabled_count": sum(1 for s in skills_info if s["is_enabled"]),
            "skills": skills_info,
        }

    def get_enabled_skills(self, user_id: str, agent_name: str) -> List[Dict]:
        """该子 agent 已启用的技能（供运行时 provider 使用）。"""
        agent = self._get_agent(user_id, agent_name)
        rows = (
            self.db.query(AgentSkill, Skill)
            .join(Skill, AgentSkill.skill_id == Skill.skill_id)
            .filter(
                AgentSkill.agent_id == agent.agent_id,
                AgentSkill.is_enabled == True,  # noqa: E712
                or_(Skill.user_id == user_id, Skill.is_builtin == True),  # noqa: E712
            )
            .all()
        )
        return [
            {
                "skill_id": skill.skill_id,
                "description": skill.description,
                "language": skill.language,
            }
            for _, skill in rows
        ]

    # ---- 启停 ----

    def enable_skill(self, user_id: str, agent_name: str, skill_id: str) -> bool:
        """启用某个子 agent 的技能：无映射则插入 is_enabled=True，有则置 True。"""
        agent = self._get_agent(user_id, agent_name)
        self._assert_skill_available(user_id, skill_id)

        agent_skill = (
            self.db.query(AgentSkill)
            .filter(
                and_(
                    AgentSkill.agent_id == agent.agent_id,
                    AgentSkill.skill_id == skill_id,
                )
            )
            .first()
        )
        if not agent_skill:
            agent_skill = AgentSkill(
                agent_id=agent.agent_id,
                skill_id=skill_id,
                is_enabled=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(agent_skill)
        else:
            agent_skill.is_enabled = True
            agent_skill.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def disable_skill(self, user_id: str, agent_name: str, skill_id: str) -> bool:
        """禁用某个子 agent 的技能（映射不存在则幂等返回）。"""
        agent = self._get_agent(user_id, agent_name)

        agent_skill = (
            self.db.query(AgentSkill)
            .filter(
                and_(
                    AgentSkill.agent_id == agent.agent_id,
                    AgentSkill.skill_id == skill_id,
                )
            )
            .first()
        )
        if agent_skill:
            agent_skill.is_enabled = False
            agent_skill.updated_at = datetime.utcnow()
            self.db.commit()
        return True

    def _assert_skill_available(self, user_id: str, skill_id: str) -> None:
        """校验技能存在且对该租户可用（owner 自定义或内置），防越权。"""
        skill = (
            self.db.query(Skill)
            .filter(
                Skill.skill_id == skill_id,
                or_(Skill.user_id == user_id, Skill.is_builtin == True),  # noqa: E712
            )
            .first()
        )
        if not skill:
            raise ValueError(
                f"Skill {skill_id} not found or user {user_id} does not have access"
            )


# 兼容别名：旧引用（历史脚本/探针）仍可用；新代码统一用 SubAgentSkillManager
WorkerSkillManager = SubAgentSkillManager
