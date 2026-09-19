"""P2 验收：手动驱动单个数据子 agent（真实 mysql_query / milvus_query）

运行：
    .venv/Scripts/python -m agent.multi_agent.demo_worker

验证口径（计划 P2 验收）：给病历号 → patient_knowledge_agent 返回结构化患者信息（真实 mysql_query）。
真实样例：病历号 1827196（search.py:151 内嵌的演示用例），crm=hn。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


async def main() -> None:
    from agent.multi_agent.agent_registry import AgentRegistry
    from agent.multi_agent.schemas import AgentTask
    from agent.multi_agent.workers import PatientKnowledgeAgent, SearchAgent
    from agent.utils.llm_service import LLMService
    from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher

    llm = LLMService.create_llm()
    dispatcher = UnifiedSkillDispatcher(llm)
    registry = AgentRegistry(dispatcher=dispatcher)
    registry.register(PatientKnowledgeAgent(llm=llm))
    registry.register(SearchAgent(llm=llm))
    print("[demo] 已注册子 agent:", list(registry.snapshot().keys()))

    state = {
        "human_input": "查询患者1827196的基本信息和最近就诊记录，同时帮我看看湿疹怎么治疗",
        "user_id": "1827196",
        "medical_record_no": "1827196",
        "crm": "hn",
        "sub_agent_input": "",
    }
    # 用例 A：合并子 agent 双通道（mysql 每轮必查病历 + milvus 知识按需检索）
    task = AgentTask(
        name="patient_knowledge_agent",
        human_input=state["human_input"],
        skill_names=["mysql_query", "milvus_query"],
    )
    res = await registry.run("patient_knowledge_agent", task, state)
    print("\n===== 合并子 agent 结果（双通道） =====")
    print(res.model_dump_json(indent=2, ensure_ascii=False))
    patient_ok = "患者" in res.content or "病历" in res.content
    knowledge_ok = "湿疹" in res.content or "治疗" in res.content
    ok = res.success and patient_ok and knowledge_ok
    print(
        f"\n[验收] 合并子 agent success={res.success} 患者段={patient_ok} 知识段={knowledge_ok} "
        f"判定={'✅' if ok else '⚠️'}"
    )

    # 用例 B：force_patient 轮（天气等场景 orchestrator 强制补查病歷）→ 只跑无条件 mysql_query
    task_b = AgentTask(
        name="patient_knowledge_agent",
        human_input="查询患者1827196的基本信息",
        skill_names=["mysql_query", "milvus_query"],
        payload={"force_patient": True},
    )
    res_b = await registry.run("patient_knowledge_agent", task_b, state)
    print("\n===== force_patient 轮结果（只查病歷） =====")
    print(res_b.model_dump_json(indent=2, ensure_ascii=False))
    patient_only_ok = res_b.success and ("患者" in res_b.content or "病历" in res_b.content)
    print(f"\n[验收] force_patient 轮 success={res_b.success} 判定={'✅' if patient_only_ok else '⚠️'}")


if __name__ == "__main__":
    asyncio.run(main())