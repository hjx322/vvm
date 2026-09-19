#!/usr/bin/env python3
"""
测试多用户多医生技能管理系统
流程：
1. 为用户创建技能1
2. 为用户创建医生
3. 医生启用技能1
4. 医生启用系统技能 milvus_query
5. 医生关闭技能1
6. 删除技能1
7. 删除医生
"""

import os
import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from backend.models import Base, Skill, Agent, AgentSkill
from backend.services import AgentManager, SkillManager, AgentSkillManager
from config.app_config import load_config


class TestSkillAndAgentManager:
    """技能与医生管理功能测试类"""

    def __init__(self):
        """初始化测试环境"""
        # 从 config 加载 MySQL 配置
        config = load_config()
        mysql_config = config.db.mysql

        # 创建 MySQL 连接字符串
        db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"

        # 初始化数据库引擎
        self.engine = create_engine(
            db_url,
            echo=False,  # 设为 True 可查看 SQL 语句
            pool_recycle=3600,  # 连接回收周期
            pool_pre_ping=True,  # 获取连接前检查是否活跃
        )
        Base.metadata.create_all(bind=self.engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db_session = SessionLocal()
        self.test_user_id = "1827196"

    def test_create_custom_skill(self):
        """测试 1: 为用户创建技能1"""
        print("\n" + "=" * 60)
        print("测试 1: 为用户创建自定义技能")
        print("=" * 60)

        manager = SkillManager(self.db_session)

        # 使用项目中现有的技能包进行测试
        skill_zip_path = Path(__file__).parent / ".claude" / "skills" / "healthfit.zip"

        if not skill_zip_path.exists():
            raise FileNotFoundError(f"测试技能包不存在: {skill_zip_path}")

        # 使用 upload_skill 方法创建技能
        skill = manager.upload_skill(
            user_id=self.test_user_id,
            zip_file_path=str(skill_zip_path),

            language="python",
        )

        # 验证技能信息
        assert skill.skill_id is not None, "❌ 技能 ID 为空"
        assert skill.user_id == self.test_user_id, "❌ 用户 ID 不匹配"
        assert not skill.is_builtin, "❌ 技能应该是自定义的"

        print(f"✅ 自定义技能创建成功")
        print(f"   技能 ID: {skill.skill_id}")
        print(f"   技能描述: {skill.description}")
        print(f"   所属用户: {skill.user_id}")
        print(f"   创建时间: {skill.created_at}")

        return skill.skill_id

    def test_create_agent(self):
        """测试 2: 为用户创建医生"""
        print("\n" + "=" * 60)
        print("测试 2: 为用户创建医生")
        print("=" * 60)

        manager = AgentManager(self.db_session)

        # 创建医生
        agent = manager.create_agent(
            user_id=self.test_user_id,
            agent_name="皮肤科医生 Dr. Liu",
        )

        # 验证医生信息
        assert agent.agent_id is not None, "❌ 医生 ID 为空"
        assert agent.agent_id.startswith("agt_"), "❌ 医生 ID 前缀错误"
        assert agent.name == "皮肤科医生 Dr. Liu", "❌ 医生名称不匹配"
        assert agent.user_id == self.test_user_id, "❌ 用户 ID 不匹配"

        print(f"✅ 医生创建成功")
        print(f"   医生 ID: {agent.agent_id}")
        print(f"   医生名称: {agent.name}")
        print(f"   所属用户: {agent.user_id}")
        print(f"   创建时间: {agent.created_at}")

        # 验证医生创建时不应该有任何 agent_skills 记录
        agent_skills = self.db_session.query(AgentSkill).filter(
            AgentSkill.agent_id == agent.agent_id
        ).all()

        print(f"\n✅ 验证医生初始状态：无自动技能映射")
        print(f"   初始技能映射数: {len(agent_skills)}")
        assert len(agent_skills) == 0, "❌ 医生不应该有初始技能映射"

        return agent.agent_id

    def test_enable_custom_skill(self, agent_id, skill_id):
        """测试 3: 医生启用自定义技能1"""
        print("\n" + "=" * 60)
        print("测试 3: 医生启用自定义技能")
        print("=" * 60)

        manager = AgentSkillManager(self.db_session)

        # 启用技能
        agent_skill = manager.enable_skill(
            agent_id=agent_id,
            skill_id=skill_id,
            user_id=self.test_user_id,
        )

        # 验证启用结果
        assert agent_skill.agent_id == agent_id, "❌ 医生 ID 不匹配"
        assert agent_skill.skill_id == skill_id, "❌ 技能 ID 不匹配"
        assert agent_skill.is_enabled is True, "❌ 技能未启用"

        print(f"✅ 自定义技能启用成功")
        print(f"   医生 ID: {agent_skill.agent_id}")
        print(f"   技能 ID: {agent_skill.skill_id}")
        print(f"   启用状态: {agent_skill.is_enabled}")
        print(f"   启用时间: {agent_skill.updated_at}")

        return agent_skill

    def test_enable_builtin_skill(self, agent_id,skill_id):
        """测试 4: 医生启用系统技能 milvus_query"""
        print("\n" + "=" * 60)
        print("测试 4: 医生启用系统技能")
        print("=" * 60)


        manager = AgentSkillManager(self.db_session)

        # 启用系统技能
        agent_skill = manager.enable_skill(
            agent_id=agent_id,
            skill_id=skill_id,
            user_id=self.test_user_id,
        )

        #

        print(f"✅ 系统技能启用成功")

        return agent_skill

    def test_disable_custom_skill(self, agent_id, skill_id):
        """测试 5: 医生关闭自定义技能1"""
        print("\n" + "=" * 60)
        print("测试 5: 医生关闭自定义技能")
        print("=" * 60)

        manager = AgentSkillManager(self.db_session)

        # 关闭技能
        agent_skill = manager.disable_skill(
            agent_id=agent_id,
            skill_id=skill_id,
            user_id=self.test_user_id,
        )

        # 验证关闭结果
        assert agent_skill.agent_id == agent_id, "❌ 医生 ID 不匹配"
        assert agent_skill.skill_id == skill_id, "❌ 技能 ID 不匹配"
        assert agent_skill.is_enabled is False, "❌ 技能未关闭"

        print(f"✅ 自定义技能关闭成功")
        print(f"   医生 ID: {agent_skill.agent_id}")
        print(f"   技能 ID: {agent_skill.skill_id}")

    def test_get_agent_details(self, agent_id):
        """获取医生详情（显示启用/禁用的技能）"""
        print("\n" + "=" * 60)
        print("医生技能状态详情")
        print("=" * 60)

        manager = AgentManager(self.db_session)

        result = manager.get_agent_details(
            user_id=self.test_user_id,
            agent_id=agent_id,
        )

        print(f"✅ 医生详情获取成功")
        print(f"   医生 ID: {result['agent_id']}")
        print(f"   医生名称: {result['agent_name']}")
        print(f"   总技能数: {result['total_skills']}")
        print(f"   启用技能数: {result['enabled_count']}")

        print(f"\n启用的技能:")
        if result["enabled_skills"]:
            for skill in result["enabled_skills"]:
                print(f"   - {skill['skill_id']}: {skill['description']}")
        else:
            print(f"   （无）")

        print(f"\n禁用的技能:")
        if result["disabled_skills"]:
            for skill in result["disabled_skills"][:5]:  # 只显示前5个
                print(f"   - {skill['skill_id']}: {skill['description']}")
            if len(result["disabled_skills"]) > 5:
                print(f"   ... 还有 {len(result['disabled_skills']) - 5} 个禁用技能")
        else:
            print(f"   （无）")

    def test_delete_custom_skill(self, skill_id):
        """测试 6: 删除自定义技能1"""
        print("\n" + "=" * 60)
        print("测试 6: 删除自定义技能")
        print("=" * 60)

        manager = SkillManager(self.db_session)

        # 删除前验证技能存在
        skill_before = self.db_session.query(Skill).filter(
            Skill.skill_id == skill_id
        ).first()
        assert skill_before is not None, "❌ 待删除技能不存在"
        print(f"✅ 待删除技能存在: {skill_before.description}")

        # 删除技能
        result = manager.delete_skill(
            user_id=self.test_user_id,
            skill_id=skill_id,
        )

        assert result is True, "❌ 删除操作返回失败"
        print(f"✅ 自定义技能删除成功")


    def test_delete_agent(self, agent_id):
        """测试 7: 删除医生"""
        print("\n" + "=" * 60)
        print("测试 7: 删除医生")
        print("=" * 60)

        manager = AgentManager(self.db_session)

        # 删除前验证医生存在
        agent_before = self.db_session.query(Agent).filter(
            Agent.agent_id == agent_id
        ).first()
        assert agent_before is not None, "❌ 待删除医生不存在"
        print(f"✅ 待删除医生存在: {agent_before.name}")

        # 删除医生
        result = manager.delete_agent(
            user_id=self.test_user_id,
            agent_id=agent_id,
        )

        assert result is True, "❌ 删除操作返回失败"
        print(f"✅ 医生删除成功")

        # 验证医生已删除
        agent_after = self.db_session.query(Agent).filter(
            Agent.agent_id == agent_id
        ).first()
        assert agent_after is None, "❌ 医生仍然存在"
        print(f"✅ 验证医生已从数据库删除")

        # 验证级联删除 agent_skills
        agent_skills = self.db_session.query(AgentSkill).filter(
            AgentSkill.agent_id == agent_id
        ).all()
        assert len(agent_skills) == 0, "❌ 医生的技能未被级联删除"
        print(f"✅ 验证医生关联的技能已级联删除")

    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "=" * 60)
        print("多用户多医生技能管理系统 - 完整流程测试")
        print("=" * 60)

        try:
            builtin_skill_id = "milvus_query"
            # 按顺序运行测试
            skill_id = self.test_create_custom_skill()
            agent_id = self.test_create_agent()
            self.test_enable_custom_skill(agent_id, skill_id)
            self.test_enable_builtin_skill(agent_id,builtin_skill_id)
            self.test_get_agent_details(agent_id)
            self.test_disable_custom_skill(agent_id, skill_id)
            self.test_enable_custom_skill(agent_id, skill_id)
            self.test_delete_custom_skill(skill_id)
            self.test_delete_agent(agent_id)

            # 总结
            print("\n" + "=" * 60)
            print("✅ 所有测试通过！")
            print("=" * 60)
            return True

        except AssertionError as e:
            print(f"\n❌ 测试失败: {e}")
            return False
        except Exception as e:
            print(f"\n❌ 意外错误: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.db_session.close()


if __name__ == "__main__":
    tester = TestSkillAndAgentManager()
    #skill_id = tester.test_create_custom_skill()
    #tester.test_enable_custom_skill("agt_d75e25a434fa457f","healthfit")
    # tester.test_enable_builtin_skill("agt_d75e25a434fa457f","web_search")
    #tester.test_enable_builtin_skill("agt_d75e25a434fa457f","mysql_query")
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
