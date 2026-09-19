# -*- coding: utf-8 -*-
"""
数字医生技能权限管理工具
用于启用/禁用医生的技能、查询权限等
"""
import argparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.services import AgentManager, AgentSkillManager


def init_db():
    """初始化数据库连接"""
    config = load_config()
    mysql_config = config.db.mysql
    db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def list_doctor_skills(agent_id, user_id):
    """列出医生的所有技能（启用和禁用）"""
    db_session = init_db()
    try:
        agent_manager = AgentManager(db_session)
        details = agent_manager.get_agent_details(user_id=user_id, agent_id=agent_id)

        print(f"\n===== 医生信息 =====")
        print(f"ID: {details['agent_id']}")
        print(f"名称: {details['agent_name']}")
        print(f"用户: {details['user_id']}")

        print(f"\n===== 启用的技能 ({details['enabled_count']}/{details['total_skills']}) =====")
        if details["enabled_skills"]:
            for i, skill in enumerate(details["enabled_skills"], 1):
                print(f"{i}. {skill['skill_id']}")
                print(f"   说明: {skill['description'][:60]}...")
        else:
            print("（无）")

        print(f"\n===== 禁用的技能 =====")
        if details["disabled_skills"]:
            for i, skill in enumerate(details["disabled_skills"], 1):
                print(f"{i}. {skill['skill_id']}")
        else:
            print("（无）")
    finally:
        db_session.close()


def enable_skill(agent_id, skill_id, user_id):
    """为医生启用技能"""
    db_session = init_db()
    try:
        skill_manager = AgentSkillManager(db_session)
        result = skill_manager.enable_skill(agent_id=agent_id, skill_id=skill_id, user_id=user_id)
        print(f"✓ 技能 '{skill_id}' 已为医生 '{agent_id}' 启用")
    except Exception as e:
        print(f"✗ 错误: {e}")
    finally:
        db_session.close()


def disable_skill(agent_id, skill_id, user_id):
    """为医生禁用技能"""
    db_session = init_db()
    try:
        skill_manager = AgentSkillManager(db_session)
        result = skill_manager.disable_skill(agent_id=agent_id, skill_id=skill_id, user_id=user_id)
        print(f"✓ 技能 '{skill_id}' 已为医生 '{agent_id}' 禁用")
    except Exception as e:
        print(f"✗ 错误: {e}")
    finally:
        db_session.close()


def batch_enable_skills(agent_id, skill_ids, user_id):
    """为医生批量启用多个技能"""
    db_session = init_db()
    try:
        skill_manager = AgentSkillManager(db_session)
        for skill_id in skill_ids:
            try:
                skill_manager.enable_skill(agent_id=agent_id, skill_id=skill_id, user_id=user_id)
                print(f"✓ 启用: {skill_id}")
            except Exception as e:
                print(f"✗ 启用失败: {skill_id} - {e}")
    finally:
        db_session.close()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="数字医生技能权限管理工具",
        prog="python manage_skills.py"
    )

    subparsers = parser.add_subparsers(dest="command", help="命令", required=True)

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出医生的所有技能")
    list_parser.add_argument("agent_id", help="医生ID")
    list_parser.add_argument("user_id", help="用户ID")

    # enable 命令
    enable_parser = subparsers.add_parser("enable", help="为医生启用技能")
    enable_parser.add_argument("agent_id", help="医生ID")
    enable_parser.add_argument("skill_id", help="技能ID")
    enable_parser.add_argument("user_id", help="用户ID")

    # disable 命令
    disable_parser = subparsers.add_parser("disable", help="为医生禁用技能")
    disable_parser.add_argument("agent_id", help="医生ID")
    disable_parser.add_argument("skill_id", help="技能ID")
    disable_parser.add_argument("user_id", help="用户ID")

    # batch-enable 命令
    batch_parser = subparsers.add_parser("batch-enable", help="为医生批量启用技能")
    batch_parser.add_argument("agent_id", help="医生ID")
    batch_parser.add_argument("user_id", help="用户ID")
    batch_parser.add_argument("skills", nargs="+", help="技能ID列表")

    args = parser.parse_args()

    if args.command == "list":
        list_doctor_skills(args.agent_id, args.user_id)
    elif args.command == "enable":
        enable_skill(args.agent_id, args.skill_id, args.user_id)
        # 显示更新后的列表
        print("\n更新后的技能列表：")
        list_doctor_skills(args.agent_id, args.user_id)
    elif args.command == "disable":
        disable_skill(args.agent_id, args.skill_id, args.user_id)
        # 显示更新后的列表
        print("\n更新后的技能列表：")
        list_doctor_skills(args.agent_id, args.user_id)
    elif args.command == "batch-enable":
        print(f"正在为医生 {args.agent_id} 批量启用技能...")
        batch_enable_skills(args.agent_id, args.skills, args.user_id)
        # 显示更新后的列表
        print("\n更新后的技能列表：")
        list_doctor_skills(args.agent_id, args.user_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
