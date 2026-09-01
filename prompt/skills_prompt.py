"""技能描述 Prompt 生成（从数据库查询，替代 npx openskills list）

v2 改进：移除 npx openskills 子进程调用，直接从 MySQL 查询启用的技能列表。
"""

from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker

from backend.models import Agent, Skill, AgentSkill
from backend.services import AgentManager
from config.app_config import load_config


def get_skills_description(user_id: str, doctor_id: str) -> str:
    """获取智能体已启用的技能描述文本（供 LLM 选择时参考）

    替代原有的 npx openskills list 调用。
    直接从 MySQL 查询该用户可用的技能 + 系统内置技能。

    Args:
        user_id: 用户 ID（租户标识）
        doctor_id: 医生/智能体 ID

    Returns:
        格式化的技能描述字符串
    """
    config = load_config()
    mysql_config = config.db.mysql

    db_url = (
        f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}"
        f"@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
    )

    engine = create_engine(
        db_url,
        echo=False,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = SessionLocal()

    try:
        manager = AgentManager(db_session)
        result = manager.get_agent_details(
            user_id=user_id,
            agent_id=doctor_id,
        )

        skill_prompt = "\n启用的技能:"
        if result["enabled_skills"]:
            for i, skill in enumerate(result["enabled_skills"]):
                skill_prompt += (
                    f"\n{i + 1}. {skill['skill_id']}: {skill['description']}"
                )
        else:
            skill_prompt += "\n（无）"

        return skill_prompt
    finally:
        db_session.close()


def get_skills_system_prompt(state) -> str:
    """获取技能系统提示词（兼容旧 SkillQueryNode）

    新架构中 UnifiedSkillDispatcher 不再使用此函数，
    但保留给旧的 SkillQueryNode 兼容使用。
    """
    medical_record_no = state.get("medical_record_no", "")
    doctor_id = state.get("doctor_id", "")

    skills = get_skills_description(
        user_id=medical_record_no,
        doctor_id=doctor_id,
    )
    return f"""你是一个**智能任务调度器**。
你的目标是分析用户输入，并输出需要执行的**所有**工具指令。

### 工具清单
    {skills}

### 调度流程 (必须严格执行)

**第一步：意图扫描与工具匹配**
扫描用户输入，匹配**所有**相关的工具（可多选）：

#### 医疗领域工具 (严格限制为医疗相关)
涉及"疾病定义"、"药物说明"、"医学知识" -> 选中 `milvus_query`

#### 外部工具 (无领域限制)


### 输出格式
仅输出指令或回复，不要有分析过程。
- 并行调用示例：
openskills read xxx
openskills read milvus_query
- 外部技能调用示例：
openskills read clawhub_weather-cn
- 拒绝示例：
REPLY: 我无法处理这个请求。

    """
