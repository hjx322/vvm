# -*- coding: utf-8 -*-
"""为医生启用 healthfit 技能"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.services import AgentSkillManager

config = load_config()
mysql_config = config.db.mysql

db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
db_session = SessionLocal()

try:
    skill_manager = AgentSkillManager(db_session)

    # 为医生启用 healthfit 技能
    result = skill_manager.enable_skill(
        agent_id="agt_d75e25a434fa457f",
        skill_id="healthfit",
        user_id="1827196",
    )

    print("✓ healthfit 技能已成功为医生启用！")
    print(f"Skill ID: {result.skill_id}")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db_session.close()
