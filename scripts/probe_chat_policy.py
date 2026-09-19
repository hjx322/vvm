# 探针：验证"可回答所有问题，危险时附仅供参考"的修改是否生效
# 用法：.venv\Scripts\python.exe scripts\probe_chat_policy.py
# 说明：不依赖 8001 进程，直接调用新代码 Supervisor.synthesize（真实 LLM 一次）
import asyncio
import sys

sys.path.insert(0, r"f:\1Github\vm")

from agent.multi_agent.orchestrator import DISCLAIMER_MEDICAL_ADVICE, _PRESCRIPTION_RE
from agent.multi_agent.supervisor import Supervisor


async def main():
    state = {
        "human_input": "深圳今天的天气怎么样，我的湿疹应该涂什药",
        "messages": [],
        "context_info": "",
        "doctor_id": "",
        "user_id": "",
        "crm": "",
        "patient_info": "",
        "patient_visit_record": "",
        "patient_examine_result": "",
        "sub_agent_input": "",
    }
    sup = Supervisor()
    # 模拟 L1 子 agent 已取回的数据（weather: search_agent；湿疹: patient_knowledge_agent）
    outputs = {
        "search_agent": "深圳今天多云，气温 26~31℃，东南风 3 级，湿度 85%，午后有雷阵雨。",
        "patient_knowledge_agent": "湿疹（特应性皮炎）急性期可短期外用糖皮质激素类药膏（如丁酸氢化可的松乳膏、糠酸莫米松乳膏），并配合保湿剂修复皮肤屏障；避免热水烫洗与搔抓。",
    }
    draft = await sup.synthesize(state, worker_outputs=outputs)
    print("===== DRAFT（supervisor 综合输出）=====")
    print(draft)
    print("===== 门卫校验 =====")
    print("regex 命中药名/剂量:", bool(_PRESCRIPTION_RE.search(draft)))
    if _PRESCRIPTION_RE.search(draft):
        final = draft.rstrip() + "\n\n" + DISCLAIMER_MEDICAL_ADVICE
        print("----- 追加免责声明后的最终回答 -----")
        print(final)
    else:
        print("未命中 regex（实际走 safety_agent 结构化复核，探针跳过），最终原样输出")


asyncio.run(main())