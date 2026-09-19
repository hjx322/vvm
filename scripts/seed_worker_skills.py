# -*- coding: utf-8 -*-
"""一次性数据脚本：把多 agent 的子 agent 技能配置写入 DB（幂等，可重复执行）→ 统一 agent 命名

背景：多 agent 化后，每个子 agent 的技能原本是硬编码类属性（如
agent/multi_agent/workers.py 里 PatientKnowledgeAgent.skills=["mysql_query"]）。
本脚本把这些子 agent 以及"每个子 agent 启用了哪些技能"落到 MySQL：

    agents      表  当作子 agent 注册表：name 列存子 agent 名（patient_knowledge_agent 等），
                     user_id 存管理租户（1827196）。
    agent_skills 表  当作子 agent-技能绑定：agent_id 指向该子 agent 行，is_enabled 控制启停。

默认写入的启停状态 = 当前代码里的硬编码技能，保证改造前后运行行为一致：
    patient_knowledge_agent -> mysql_query + milvus_query（原 patient_worker/knowledge_worker 合并：
                                mysql 每轮必查 + milvus 知识按需检索）
    search_agent -> web_search
    imaging_agent -> derma_image
    general_agent -> web_search（+ skills 表已有 clawhub_weather-cn 则追加 → 多技能池触发三级匹配）
    safety_agent -> 无绑定（纯 reasoning agent，不执行技能）
    （旧 *_worker 行会被「改名迁移」原地 UPDATE 为 *_agent，agent_id 与绑定保留）

运行时（agent/multi_agent/worker_skill_provider.py）按 Agent.name == agent_name
反查该子 agent 启用的技能，替换硬编码 self.skills。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/seed_worker_skills.py
"""
import os
import sys
import uuid
from datetime import datetime

# 让脚本可从仓库根目录直接 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import and_, or_, text

from backend.database.session_factory import init_db_session, get_session_context
from backend.models import Agent, AgentSkill, Skill

# 管理租户 id（与前端 App.vue 的 userId 一致，勿改成患者号）
USER_ID = "1827196"

# 内置技能（SKILL_REGISTRY 键名，见 skills/registry.py），需保证 skills 表有 is_builtin=True 行
BUILTIN_SKILLS = {
    "mysql_query": "查询患者档案、就诊记录、检查结果（真实医疗数据）",
    "milvus_query": "检索疾病/药物/护理的医学知识库",
    "web_search": "互联网联网检索最新信息",
    "derma_image": "皮肤图像识别（上传皮肤照片/描述皮肤病症状时）",
}

# 子 agent 名 -> 当前硬编码技能（safety 为空，reasoning agent 不执行技能；
# 原 followup_worker / diagnosis_worker 已移除——诊断职责并入 supervisor 综合）
AGENT_SKILLS = {
    "patient_knowledge_agent": ["mysql_query", "milvus_query"],
    "search_agent": ["web_search"],
    "imaging_agent": ["derma_image"],
    "general_agent": ["web_search"],  # 通用助手：闲聊/资讯/天气（+ 已有天气技能则追加）
    "safety_agent": [],
}

# 通用 agent 默认追加可用的天气自定义技能（存在于 skills 表时，技能池 >= 2 触发真实三级匹配）
GENERAL_AGENT_EXTRA_SKILLS = ("clawhub_weather-cn",)

# worker→agent 改名映射（agents 表原地迁移，保留 agent_id 与 agent_skills 绑定；幂等可回退）
_RENAME_OLD_TO_NEW = {
    "patient_worker": "patient_knowledge_agent",
    "knowledge_worker": "patient_knowledge_agent",
    "patient_knowledge_worker": "patient_knowledge_agent",
    "search_worker": "search_agent",
    "imaging_worker": "imaging_agent",
    "safety_worker": "safety_agent",
}


def _ensure_builtin_skills(db) -> None:
    """补齐 4 个内置技能到 skills 表（is_builtin=True），保证 worker 绑定有 skill_id 可引用。"""
    for sid, desc in BUILTIN_SKILLS.items():
        row = (
            db.query(Skill)
            .filter(and_(Skill.skill_id == sid, Skill.is_builtin == True))  # noqa: E712
            .first()
        )
        if not row:
            db.add(
                Skill(
                    skill_id=sid,
                    description=desc,
                    language="python3",
                    is_builtin=True,
                    user_id=USER_ID,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
    db.flush()


def _ensure_agent_rows(db) -> dict:
    """为每个子 agent 各插入一条 agents 行（name=子 agent 名），返回子 agent 名 -> Agent 行。"""
    agents = {}
    for name in AGENT_SKILLS:
        row = (
            db.query(Agent)
            .filter(and_(Agent.name == name, Agent.user_id == USER_ID))
            .first()
        )
        if not row:
            row = Agent(
                agent_id=f"agt_{uuid.uuid4().hex[:16]}",
                name=name,
                user_id=USER_ID,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            db.flush()
        agents[name] = row
    db.flush()
    return agents


def _seed_bindings(db, agents: dict) -> None:
    """按硬编码技能为每个子 agent 预置 agent_skills 绑定（is_enabled=True），
    并给 general_agent 追加已有天气自定义技能。"""
    for name, skill_ids in AGENT_SKILLS.items():
        agent = agents[name]
        # general_agent：若 skills 表已存在天气技能，则加入技能池（触发真实三级匹配）
        extra = []
        if name == "general_agent" and GENERAL_AGENT_EXTRA_SKILLS:
            for sid in GENERAL_AGENT_EXTRA_SKILLS:
                exists = (
                    db.query(Skill)
                    .filter(
                        and_(
                            Skill.skill_id == sid,
                            or_(
                                Skill.user_id == USER_ID,
                                Skill.is_builtin == True,  # noqa: E712
                            ),
                        )
                    )
                    .first()
                )
                if exists:
                    extra.append(sid)
            skill_ids = list(skill_ids) + extra
        for sid in skill_ids:
            exist = (
                db.query(AgentSkill)
                .filter(
                    and_(
                        AgentSkill.agent_id == agent.agent_id,
                        AgentSkill.skill_id == sid,
                    )
                )
                .first()
            )
            if not exist:
                db.add(
                    AgentSkill(
                        agent_id=agent.agent_id,
                        skill_id=sid,
                        is_enabled=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                )


def _migrate_agent_names(db) -> None:
    """把旧 *_worker 行原地改名为 *_agent（幂等，保留 agent_id 与 agent_skills 绑定，可回退）。

    相比旧策略（删旧行重建）保留 agent_id：不改名时前端已绑定/启停的技能不受影响；
    对缺行 no-op（全新库无需迁移）。
    """
    migrated = []
    for old, new in _RENAME_OLD_TO_NEW.items():
        if old == new:
            continue
        agent = (
            db.query(Agent)
            .filter(and_(Agent.name == old, Agent.user_id == USER_ID))
            .first()
        )
        if not agent:
            continue
        # 若目标新名已存在（此前迁移过/re-run），则跳过（避免同租户两行同名）
        exists_new = (
            db.query(Agent)
            .filter(and_(Agent.name == new, Agent.user_id == USER_ID))
            .first()
        )
        if exists_new and exists_new.agent_id != agent.agent_id:
            # 目标已存在但非同一条 → 合并：把旧行 agent_skills 绑定转移给新行后删除旧行
            for sk in db.query(AgentSkill).filter(AgentSkill.agent_id == agent.agent_id).all():
                dup = (
                    db.query(AgentSkill)
                    .filter(
                        and_(AgentSkill.agent_id == exists_new.agent_id, AgentSkill.skill_id == sk.skill_id)
                    )
                    .first()
                )
                if not dup:
                    db.add(
                        AgentSkill(
                            agent_id=exists_new.agent_id,
                            skill_id=sk.skill_id,
                            is_enabled=sk.is_enabled,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                    )
            db.query(AgentSkill).filter(AgentSkill.agent_id == agent.agent_id).delete()
            db.delete(agent)
            agent = exists_new
        elif exists_new is None:
            agent.name = new
        migrated.append(new)
        db.flush()
    if migrated:
        print(f"[INFO] 旧 *_worker 行已迁移为 *_agent（agent_id/绑定保留）: {migrated}")
    db.flush()


def _add_name_unique_index(bind) -> None:
    """为 agents.name 加唯一索引（幂等）。

    注意：可能因既有旧医生行存在重名（如多条"皮肤科DrWang"）而无法建索引。
    worker 名唯一性由本 seed 脚本保证（按 name+user_id 幂等 upsert），
    故索引建不建都不影响运行时按 Agent.name 定位 worker；此处尽力而为即可。
    """
    # 已存在同名唯一索引则跳过
    with bind.connect() as conn:
        exists = any(
            row["Key_name"] == "uq_agents_name" and row["Non_unique"] == 0
            for row in conn.execute(text("SHOW INDEX FROM agents")).mappings()
        )
    if exists:
        return
    try:
        with bind.begin() as conn:
            conn.execute(text("ALTER TABLE agents ADD UNIQUE INDEX uq_agents_name (name)"))
        print("[OK] agents.name 唯一索引已创建")
    except Exception as e:  # noqa: BLE001
        print(f"[INFO] agents.name 唯一索引未创建（既有重名脏数据，不影响 worker 运行）: {e}")


def main() -> None:
    bind = init_db_session()

    with get_session_context() as db:
        _ensure_builtin_skills(db)
        # worker→agent 改名迁移（先迁移再 ensure/seed：旧行绑定保留，新名行幂等补绑，保证不重复）
        _migrate_agent_names(db)
        agents = _ensure_agent_rows(db)
        _seed_bindings(db, agents)
        db.commit()

        print("[OK] 子 agent 技能注册完成：")
        for name, agent in agents.items():
            sids = AGENT_SKILLS[name]
            bind_count = (
                db.query(AgentSkill)
                .filter(
                    and_(
                        AgentSkill.agent_id == agent.agent_id,
                        AgentSkill.is_enabled == True,  # noqa: E712
                    )
                )
                .count()
            )
            print(
                f"     - {name} ({agent.agent_id})：已启用 {bind_count} 个技能"
                + (f" {sids}" if sids else "（无）")
            )

    _add_name_unique_index(bind)


if __name__ == "__main__":
    main()
