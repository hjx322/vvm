"""Supervisor：任务规划 + 最终综合（计划 P2）

- plan()      ：with_structured_output(SupervisorPlan)，决定本轮唤哪些 worker
  （链路同 skill_dispatcher.py:166 已验证；纯闲聊时 needs_workers=False 直达综合）
- synthesize():把 worker 结果合成为合规回答，复用 PROMPT_CHAT_DERMATOLOGIST 全部
  合规条款（不处方/不用"患者"/普通文本/谨慎措辞）。
"""

from datetime import datetime
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate
from langgraph.config import get_stream_writer

from agent.core.state import DigitalSmartDoctorState
from agent.multi_agent.prompts import (
    SUPERVISOR_PLAN_SYSTEM_PROMPT,
    AGENT_DESCRIPTIONS_TEXT,
)
from agent.multi_agent.schemas import SupervisorPlan
from agent.utils.llm_service import LLMService
from prompt.chat_prompt import PROMPT_CHAT_DERMATOLOGIST


class Supervisor:
    """Supervisor：决策者（qwen-max）+ 合规综合"""

    def __init__(self, plan_llm=None, synth_llm=None):
        # 两个 LLM 都用 supervisor 角色分级模型（qwen-max，结构化更稳）
        self.plan_llm = plan_llm or LLMService.create_agent_llm("supervisor")
        self.synth_llm = synth_llm or LLMService.create_agent_llm("supervisor")

    async def plan(
        self,
        state: DigitalSmartDoctorState,
        agent_descriptions: Optional[str] = None,
    ) -> SupervisorPlan:
        """结构化规划本轮任务（qwen-max）

        Args:
            state: 智能体状态
            agent_descriptions: 动态生成的子 agent 能力描述（含各 agent 当前启用技能 + 任务要点）；
                                 为 None 时用 prompts.py 静态 AGENT_DESCRIPTIONS_TEXT 降级。
        """
        desc_text = agent_descriptions or AGENT_DESCRIPTIONS_TEXT
        sys_prompt = SUPERVISOR_PLAN_SYSTEM_PROMPT.format(worker_descriptions=desc_text)
        messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=str(state.get("human_input", ""))),
        ]
        structured = self.plan_llm.with_structured_output(SupervisorPlan)
        plan = await structured.ainvoke(messages)
        return plan

    @staticmethod
    def _build_sub_agent_input(
        worker_outputs: Optional[Dict[str, str]] = None,
        direct_reply: Optional[str] = None,
    ) -> str:
        """把各 worker 结果 / direct_reply 拼成 sub_agent_input 风格输入

        诊断聚合已并入 supervisor：L1 worker 结果（patient/knowledge/search...）直接作为
        综合语料，supervisor 基于它们形成鉴别诊断与合规回答。
        """
        parts = []
        if direct_reply:
            parts.append(direct_reply)
        for name, out in (worker_outputs or {}).items():
            if out:
                parts.append(f"### {name}\n{out}")
        return "\n\n---\n\n".join(parts) or "（本轮无需外部数据）"

    async def synthesize(
        self,
        state: DigitalSmartDoctorState,
        worker_outputs: Optional[Dict[str, str]] = None,
        direct_reply: Optional[str] = None,
    ) -> str:
        """生成完整合规回答草稿（qwen-max，非流式生成）

        生成后由 orchestrator 交给 safety_agent 审批，审批通过后才 stream_output
        —— 确保用户看到的永远是经过合规门卫的文本（拦截须前置到输出前）。
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        chat_history = state.get("messages") or []
        context_info = state.get("context_info") or ""
        sub_agent_input = self._build_sub_agent_input(worker_outputs, direct_reply)

        # 锚定皮肤科合规模板（与单agent链路的合规口径统一）
        _sys_prompt = PromptTemplate(
            template=PROMPT_CHAT_DERMATOLOGIST,
            input_variables=[
                "current_time",
                "chat_history",
                "patient_outpatient_record",
                "sub_agent_input",
                "context_info",
            ],
        ).format(
            current_time=current_time,
            chat_history=chat_history,
            patient_outpatient_record="",
            sub_agent_input=sub_agent_input,
            context_info=context_info,
        )

        messages = [
            SystemMessage(content=_sys_prompt),
            HumanMessage(content=str(state.get("human_input", ""))),
        ]
        # 兜底：非 str content 做 surrogatepass 清洗，避免 checkpoint 序列化问题
        for msg in messages:
            if not isinstance(msg.content, str):
                msg.content = msg.content.encode("utf-8", "surrogatepass").decode(
                    "utf-8", "replace"
                )
        resp = await self.synth_llm.ainvoke(messages)
        return str(resp.content)

    @staticmethod
    def stream_output(text: str) -> None:
        """把已经过合规审批的最终文本流式输出给前端

        synthesize 不再自行流式 —— 合规审批必须发生在输出之前
        """
        try:
            writer = get_stream_writer()
        except Exception:
            return
        if writer:
            writer({"type": "content", "content": text})