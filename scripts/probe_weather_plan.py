# 探针：定位"天气问题为何查不到数据"——复现 supervisor 规划 + search_agent 实际执行
# 用法：.venv\Scripts\python.exe scripts\probe_weather_plan.py
import asyncio
import sys

sys.path.insert(0, r"f:\1Github\vm")

from agent.multi_agent.agent_registry import AgentRegistry
from agent.multi_agent.schemas import AgentTask
from agent.multi_agent.supervisor import Supervisor
from agent.multi_agent.worker_skill_provider import AgentSkillProvider
from agent.multi_agent.workers import build_all_agents
from agent.utils.llm_service import LLMService
from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher


async def main():
    llm = LLMService.create_llm()
    dispatcher = UnifiedSkillDispatcher(llm)
    skill_provider = AgentSkillProvider()
    registry = AgentRegistry(dispatcher=dispatcher, skill_provider=skill_provider)
    for agent in build_all_agents(dispatcher=dispatcher):
        registry.register(agent)

    # 1) DB 配置下各子 agent 实际启用技能
    desc = await registry.build_descriptions()
    print("===== 子 agent 描述（含 DB 启用的技能 + 任务要点） =====")
    print(desc)
    print()

    # 2) supervisor 规划
    state = {
        "human_input": "深圳今天的天气怎么样，我的湿疹应该涂什药",
        "messages": [],
        "context_info": "",
        "doctor_id": "",
        "user_id": "1827196",
        "crm": "",
        "patient_info": "",
        "patient_visit_record": "",
        "patient_examine_result": "",
        "sub_agent_input": "",
        "medical_record_no": "",
    }
    sup = Supervisor()
    plan = await sup.plan(state, agent_descriptions=desc)
    print("===== supervisor 规划 =====")
    print("needs_workers:", plan.needs_workers)
    print("workers:", plan.worker_names)
    print("needs_medication_safety:", plan.needs_medication_safety)
    print("direct_reply:", plan.direct_reply)
    print()

    # 3) 若选了 search_agent，真实执行一次看能否查到天气
    if "search_agent" in plan.worker_names:
        sa = registry.get("search_agent")
        task = AgentTask(
            name="search_agent",
            human_input="深圳今天的天气怎么样",
            skill_names=list(sa.skills),
            payload={},
        )
        res = await sa.run(task, state)
        print("===== search_agent 实际执行结果 =====")
        print("success:", res.success)
        print("content:")
        print((res.content or "")[:1500])
    else:
        print("search_agent 未被 supervisor 选中 —— 这就是天气查不到的原因（没有联网派发）")


asyncio.run(main())