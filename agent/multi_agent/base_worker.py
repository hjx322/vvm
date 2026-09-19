"""子 agent 基类（计划 P2 → agent 化统一命名）

每个子 agent = 独立角色：独立 system prompt + 独立上下文（keep_history） + 模型分级（role）。
数据类 agent 无状态（可重放取数器）；reasoning 类 agent（safety 推理）不保留独立历史。
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from loguru import logger

from langchain_core.messages import AIMessage, BaseMessage

from agent.core.state import DigitalSmartDoctorState
from agent.multi_agent.schemas import AgentResult, AgentTask
from agent.multi_agent.skill_executor import SkillExecutor
from agent.utils.llm_service import LLMService


class BaseAgent(ABC):
    """子 agent 基类：技能执行 + 独立上下文 + 模型分级

    Attributes:
        role: 模型分级键（supervisor/safety/worker），见 create_agent_llm
        name: 子 agent 唯一名（registry 键）
        description: 供 Supervisor 规划时的功能说明
        skills: 依赖的技能名（mysql_query/milvus_query/web_search/derma_image…）
        system_prompt: 该 agent 仅针对一个子任务的角色提示词（注入 Tier3 技能选择 + supervisor 规划要点）
        keep_history: 是否保留独立对话历史（数据类 agent=False，随时可重放）
        history_cap: 独立历史截断条数（默认 6）
    """

    role: str = "worker"  # 模型档位键（保留，勿改：对应 llm.roles.worker）
    name: str = ""
    description: str = ""
    skills: List[str] = []
    # 无条件执行、绕过三级选择的技能（如每轮必查病歷的 mysql_query）
    always_skills: List[str] = []
    # 每个子 agent 仅针对一个子任务的角色提示词（简洁明确，指令跟随更好）
    system_prompt: str = ""
    # 局部上下文投影白名单：agent 只看到职责字段（None = 透传全量 state，兼容现状）
    context_fields: Optional[List[str]] = None
    keep_history: bool = False
    history_cap: int = 6

    def __init__(self, llm=None, dispatcher=None, skill_provider=None):
        # 未显式传入 LLM 时，按角色分级创建独立模型实例
        self.llm = llm if llm else LLMService.create_agent_llm(self.role)
        # 注入技能执行薄壳（复用 UnifiedSkillDispatcher 内核）
        if dispatcher:
            self.executor = SkillExecutor(dispatcher)
        else:
            self.executor = None
        # 注入技能配置提供器（从 DB 读该子 agent 启用的技能；缺省为 None → 用 self.skills 兜底）
        self.skill_provider = skill_provider

    def get_history(self, state) -> List[BaseMessage]:
        """读取该子 agent 的独立对话历史（截断到 history_cap）"""
        if not self.keep_history:
            return []
        return state.get("worker_messages", {}).get(self.name, [])[-self.history_cap :]

    def set_history(self, state, msgs) -> dict:
        """把本轮对话追加进独立历史（截断到 history_cap），返回 state 更新片段"""
        if not self.keep_history:
            return {}
        prev = list(state.get("worker_messages", {}).get(self.name, [])) + list(msgs)
        merged = prev[-self.history_cap :]
        wm = dict(state.get("worker_messages", {}))
        wm[self.name] = merged
        return {"worker_messages": wm}

    async def _exec_skills(
        self,
        task: AgentTask,
        state: dict,
        skill_names: List[str],
        run_selectable: bool = True,
    ) -> AgentResult:
        """批量执行该子 agent 的技能并聚合（复用 dispatcher 内核）

        技能分流（向右合并 agent 用）：
          - always_skills 中的技能：无条件执行、绕过三级选择（如每轮必查病歷的 mysql_query）；
          - 其余可池技能：三级技能选择（关键词/FTS5/LLM）后并行执行命中子集。
        技能名优先级：DB 配置（skill_provider 按 agent 名读启用技能）> 传入的默认 skill_names。
        刻意保留 skill_names 参数作为无 provider 时的兜底（legacy/debug & 推理 agent）。
        always_skills 为空的 agent（search/imaging/safety）走 selectable 分支，与旧行为等价。
        """
        if self.executor is None:
            return AgentResult(name=self.name, success=False, content="SkillExecutor 未注入")

        # 有 DB 配置提供器时，用它替换硬编码技能名（即时反映前端启停）
        effective_skills = skill_names
        if self.skill_provider is not None:
            db_skills = await self.skill_provider.get_enabled_skill_names(self.name)
            if db_skills is None:
                logger.warning(f"[{self.name}] skill_provider 查询失败，回退默认技能 {skill_names}")
            else:
                effective_skills = db_skills

        if not effective_skills:
            # 该 agent 全禁用或本就无技能绑定：无可执行技能 → 跳过（非失败）
            return AgentResult(name=self.name, success=False, content="", skipped=True)

        always = [s for s in effective_skills if s in (self.always_skills or [])]
        selectable = [s for s in effective_skills if s not in (self.always_skills or [])]
        if not run_selectable:
            # force 轮（如天气问题强制补查病歷时）：只跑无条件技能，跳过可池技能
            selectable = []

        successful: List[str] = []
        failed: List[str] = []
        skipped = False
        # 1) 无条件技能：直接并行执行，不做技能选择
        if always:
            r = await self.executor.execute_skills(always, state, task.human_input)
            successful += list(r["successful"])
            failed += list(r["failed"])
        # 2) 可池技能：三级技能调度（关键词/FTS5/LLM 选择 + 并行执行命中子集），
        #    注入本 agent 的 system_prompt（身份/职责），命中层级更聚焦
        if selectable:
            r = await self.executor.select_and_execute(
                selectable, task.human_input, state, agent_prompt=self.system_prompt
            )
            successful += list(r["successful"])
            failed += list(r["failed"])
            skipped = bool(r.get("skipped"))

        parts = list(successful)
        if failed:
            parts.append("；".join(failed))
        is_skipped = skipped and not successful and not failed
        return AgentResult(
            name=self.name,
            success=bool(successful),
            # 跳过时 content 必须为空：本轮根本没查过，填"未查询到结果"是错误语义
            # （那是"查了没有"的占位串，会被 supervisor 当语料读，误导综合）
            content="" if is_skipped else ("\n\n---\n\n".join(parts) or "未查询到结果"),
            # 跳过 = 本轮一个技能都没跑，且原因不是失败（LLM 判定无需技能 / 池空 /
            # 全被前置条件剪枝）。不与 success=False 混同：编排层据此显示"跳过"
            # 而非"结果异常"。
            skipped=is_skipped,
        )

    async def _chat_once(self, state, messages) -> AgentResult:
        """LLM 一轮对话，并把本轮 messages+回复记入独立历史（reasoning agent 用）

        Returns:
            AgentResult，history_update 携带 worker_messages 变更片段，供 orchestrator 合并
        """
        resp = str((await self.llm.ainvoke(messages)).content)
        hu = self.set_history(state, messages + [AIMessage(content=resp)])
        return AgentResult(
            name=self.name,
            success=True,
            content=resp,
            history_update=hu or None,
        )

    @abstractmethod
    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        """执行任务，返回结构化结果"""
        return NotImplemented