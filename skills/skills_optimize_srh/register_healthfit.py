# -*- coding: utf-8 -*-
"""注册 healthfit 技能到数据库"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.models import Skill

config = load_config()
mysql_config = config.db.mysql

db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
db_session = SessionLocal()

try:
    # 检查 healthfit 是否已存在
    existing = db_session.query(Skill).filter(Skill.skill_id == "healthfit").first()
    if existing:
        print("healthfit 技能已存在，跳过注册")
    else:
        # 注册 healthfit 技能
        new_skill = Skill(
            skill_id="healthfit",
            user_id="1827196",
            description="Personal comprehensive health management system integrating Western medicine and TCM. "
                       "Supports fitness tracking, nutrition advice, health data analysis, TCM constitution identification.",
            is_builtin=False,
            current_path="user_skills/1827196/healthfit/current",
        )
        db_session.add(new_skill)
        db_session.commit()
        print("healthfit 技能已成功注册到数据库")

    # 为医生启用 healthfit 技能
    from backend.services import AgentSkillManager
    skill_manager = AgentSkillManager(db_session)
    result = skill_manager.enable_skill(
        agent_id="agt_d75e25a434fa457f",
        skill_id="healthfit",
        user_id="1827196",
    )
    print("✓ healthfit 技能已成功为医生启用")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db_session.close()
