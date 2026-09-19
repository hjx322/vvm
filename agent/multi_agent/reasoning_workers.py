"""L2/L3 reasoning 子 agent（计划 P2 → agent 化统一命名）

- safety_agent  ：L3，用药合规门卫（复用 SAFETY_SYSTEM_PROMPT 校验，keep_history=False）

注：
- 原 followup_worker（随访/复查时间推理）已移除——职责与 supervisor 综合重叠。
- 原 diagnosis_worker（L2 诊断聚合）已移除——职责并入 supervisor 综合：
  supervisor.synthesize 直接基于 L1 数据 agent 结果（worker_outputs）形成鉴别诊断
  与合规回答，避免与 supervisor 重复综合；安全门卫仍独立（safety_agent）。
"""

from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agent.core.state import DigitalSmartDoctorState
from agent.multi_agent.base_worker import BaseAgent
from agent.multi_agent.prompts import SAFETY_SYSTEM_PROMPT
from agent.multi_agent.schemas import SafetyVerdict, AgentResult, AgentTask


class SafetyAgent(BaseAgent):
    """L3 · 用药合规门卫：结构化复核回答是否出现药名/剂量/开药表述"""

    role: str = "safety"
    name: str = "safety_agent"
    description: str = "用药合规门卫：复核回答是否出现药名/剂量/开药表述"
    # 独立 system prompt：仅针对"用药合规复核"这一子任务（提示词单一）
    system_prompt: str = SAFETY_SYSTEM_PROMPT
    keep_history: bool = False

    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content="待复核回答：\n" + str(task.human_input or "")),
        ]
        # 结构化判定：输出 SafetyVerdict{blocked, violations, reason}
        # （相比旧的字符串匹配，可解释性更强，供 safety_verdict 审计）
        structured = self.llm.with_structured_output(SafetyVerdict)
        verdict = await structured.ainvoke(messages)
        if verdict.blocked:
            content = "违规" + ("：" + "；".join(verdict.violations) if verdict.violations else "")
        else:
            content = "通过"
        return AgentResult(name=self.name, success=True, content=content)


def build_reasoning_agents(
    llm=None,
    dispatcher=None,
) -> List[BaseAgent]:
    """L2/L3 reasoning agent 集合（仅安全门卫：诊断聚合已并入 supervisor 综合）"""
    return [
        SafetyAgent(llm=llm, dispatcher=dispatcher),
    ]