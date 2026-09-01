# -*- coding: utf-8 -*-
"""
演示脚本：展示医生技能权限管理
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.services import AgentManager, AgentSkillManager

# 初始化数据库
config = load_config()
mysql_config = config.db.mysql
db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
db_session = SessionLocal()

agent_id = "agt_d75e25a434fa457f"
user_id = "1827196"

try:
    print("=" * 60)
    print("[医生技能权限管理演示]")
    print("=" * 60)

    agent_manager = AgentManager(db_session)
    skill_manager = AgentSkillManager(db_session)

    # 1. 查看医生的技能状态
    print("\n[1] 查看医生的技能状态")
    print("-" * 60)
    details = agent_manager.get_agent_details(user_id=user_id, agent_id=agent_id)
    print(f"医生: {details['agent_name']} ({agent_id})")
    print(f"\n启用的技能 ({details['enabled_count']}/{details['total_skills']}):")
    for skill in details["enabled_skills"]:
        print(f"  - {skill['skill_id']}")

    print(f"\n禁用的技能:")
    if details["disabled_skills"]:
        for skill in details["disabled_skills"]:
            print(f"  - {skill['skill_id']}")
    else:
        print("  (无)")

    # 2. 禁用一个技能
    print("\n\n[2] 禁用 healthfit 技能")
    print("-" * 60)
    print("执行: skill_manager.disable_skill(...)")
    skill_manager.disable_skill(
        agent_id=agent_id,
        skill_id="healthfit",
        user_id=user_id,
    )
    print("已禁用: healthfit")

    # 3. 查看更新后的技能状态
    print("\n[3] 查看更新后的技能状态")
    print("-" * 60)
    details = agent_manager.get_agent_details(user_id=user_id, agent_id=agent_id)
    print(f"启用的技能 ({details['enabled_count']}/{details['total_skills']}):")
    for skill in details["enabled_skills"]:
        print(f"  - {skill['skill_id']}")

    print(f"\n禁用的技能:")
    for skill in details["disabled_skills"]:
        print(f"  - {skill['skill_id']}")

    # 4. 重新启用技能
    print("\n[4] 重新启用 healthfit 技能")
    print("-" * 60)
    print("执行: skill_manager.enable_skill(...)")
    skill_manager.enable_skill(
        agent_id=agent_id,
        skill_id="healthfit",
        user_id=user_id,
    )
    print("已启用: healthfit")

    # 5. 最终状态
    print("\n[5] 最终技能状态")
    print("-" * 60)
    details = agent_manager.get_agent_details(user_id=user_id, agent_id=agent_id)
    print(f"启用的技能 ({details['enabled_count']}/{details['total_skills']}):")
    for skill in details["enabled_skills"]:
        print(f"  - {skill['skill_id']}")

    print("\n" + "=" * 60)
    print("演示完成！医生现在可以调用已启用的技能。")
    print("未启用的技能无法被调用（即使 LLM 试图调用也会被拦截）。")
    print("=" * 60)

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db_session.close()
