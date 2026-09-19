# -*- coding: utf-8 -*-
"""直接查询数据库中的技能配置"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.services import AgentManager

config = load_config()
mysql_config = config.db.mysql

# 创建数据库连接
db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
db_session = SessionLocal()

try:
    manager = AgentManager(db_session)

    # 查询医生的详细信息和启用的技能
    result = manager.get_agent_details(
        user_id="1827196",  # medical_record_no
        agent_id="agt_d75e25a434fa457f",  # doctor_id
    )

    print("===== 医生信息 =====")
    print(f"Agent ID: {result.get('agent_id')}")
    print(f"Agent Name: {result.get('agent_name')}")
    print(f"\n===== 启用的技能列表 =====")

    if result["enabled_skills"]:
        for i, skill in enumerate(result["enabled_skills"], 1):
            print(f"\n{i}. {skill['skill_id']}")
            print(f"   描述: {skill['description']}")
    else:
        print("（无启用技能）")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db_session.close()
