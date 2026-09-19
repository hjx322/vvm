# -*- coding: utf-8 -*-
"""一次性数据脚本：把默认医生（皮肤科医生 Dr. Liu）名下所有技能置为已启用。

口径与 AgentManager.get_agent_details / SkillManager.list_user_skills 一致：
    该用户的全部技能 = is_builtin=True 或 user_id=指定值的 skills 记录。
对默认医生：
    - agent_skills 无映射的技能 → 补插 AgentSkill(is_enabled=True)
    - 已有映射 → 将 is_enabled 置为 True
仅作用于 DEFAULT_AGENT_ID，其余医生不动（可在管理界面手动启停）。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/enable_default_doctor_skills.py
"""
import os
import sys
from datetime import datetime

# 让脚本可从仓库根目录直接 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import and_, or_

from backend.database.session_factory import init_db_session, get_session_context
from backend.models import Agent, AgentSkill, Skill

USER_ID = "1827196"
# 皮肤科医生 Dr. Liu（前端默认医生）
DEFAULT_AGENT_ID = "agt_d75e25a434fa457f"


def main() -> None:
    init_db_session()

    with get_session_context() as db:
        agent = db.query(Agent).filter(
            and_(Agent.agent_id == DEFAULT_AGENT_ID, Agent.user_id == USER_ID)
        ).first()
        if not agent:
            raise SystemExit(
                f"[ERROR] 默认医生 {DEFAULT_AGENT_ID} 不存在或不属于用户 {USER_ID}"
            )

        # 该用户可见的全部技能（内置 + 自定义），与 skill_manager.list_user_skills 同口径
        skills = db.query(Skill).filter(
            or_(Skill.user_id == USER_ID, Skill.is_builtin == True)  # noqa: E712
        ).all()

        added, updated = 0, 0
        enabled_ids = []
        for s in skills:
            row = db.query(AgentSkill).filter(
                and_(
                    AgentSkill.agent_id == agent.agent_id,
                    AgentSkill.skill_id == s.skill_id,
                )
            ).first()
            if row:
                if not row.is_enabled:
                    row.is_enabled = True
                    row.updated_at = datetime.utcnow()
                    updated += 1
            else:
                db.add(
                    AgentSkill(
                        agent_id=agent.agent_id,
                        skill_id=s.skill_id,
                        is_enabled=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                )
                added += 1
            enabled_ids.append(s.skill_id)
        db.commit()

        print(f"[OK] {agent.name}（{agent.agent_id}）已启用 {len(enabled_ids)} 个技能")
        print(f"     新增映射 {added} 条，更新既有 {updated} 条。")
        for sid in enabled_ids:
            print(f"     - {sid}")


if __name__ == "__main__":
    main()