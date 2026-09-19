"""子 agent 注册表（计划 P2 → agent 化统一命名）

登记 dict[str, BaseAgent] + 给未注入 executor 的 agent 注入 SkillExecutor + 按名分发任务。
"""

from typing import Dict, List, Optional

from agent.multi_agent.base_worker import BaseAgent
from agent.multi_agent.schemas import AgentResult, AgentTask
from agent.multi_agent.skill_executor import SkillExecutor


class AgentRegistry:
    """子 agent 注册表：登记 agent + 注入 SkillExecutor / AgentSkillProvider + 分发任务"""

    def __init__(self, dispatcher=None, skill_provider=None):
        self.dispatcher = dispatcher
        self.skill_provider = skill_provider
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """注册子 agent；若其尚未注入 executor/provider 且注册表持有，则注入"""
        if agent.executor is None and self.dispatcher is not None:
            agent.executor = SkillExecutor(self.dispatcher)
        if agent.skill_provider is None and self.skill_provider is not None:
            agent.skill_provider = self.skill_provider
        self._agents[agent.name] = agent

    def get(self, name: str) -> Optional[BaseAgent]:
        return self._agents.get(name)

    def get_names(self) -> List[str]:
        return list(self._agents)

    def snapshot(self) -> Dict[str, str]:
        """子 agent 名 → 功能描述（供 Supervisor 规划注入）"""
        return {n: w.description for n, w in self._agents.items()}

    async def build_descriptions(self) -> str:
        """动态生成子 agent 描述文本（含各 agent 当前启用的技能 + 任务要点），供 Supervisor 规划注入。

        相比静态 AGENT_DESCRIPTIONS_TEXT，能让 supervisor 感知每个 agent 当前
        启停状态（如"无可用技能"则标注并让 orchestrator 跳过）。查询失败降级为
        agent 类属性 description（不带技能行）。
        """
        import logging

        logger = logging.getLogger("agent_registry")
        lines = []
        for name, agent in self._agents.items():
            desc = agent.description or name
            skills_hint = ""
            if agent.skill_provider is not None:
                try:
                    names = await agent.skill_provider.get_enabled_skill_names(name)
                    if names:
                        skills_hint = f"，启用技能: {', '.join(names)}"
                    else:
                        # 查询成功但为空 = 全禁用；或 DB 查询失败返回 None → 不标注技能
                        pass
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"build_descriptions 取 {name} 技能失败: {e}")
            lines.append(f"- `{name}`：{desc}{skills_hint}")
            # 追加该 agent 的 system_prompt 要点（明确职责边界，规划更聚焦、少误派）。
            # 直接拼接、不进 .format 模板，防花括号报错；截断 160 字防 supervisor prompt 膨胀。
            sp = getattr(agent, "system_prompt", "") or ""
            if sp:
                hint = " ".join(sp.split())[:160]
                lines.append(f"  · 任务要点：{hint}")
        return "\n".join(lines) or "（可用子 agent 为空）"

    async def run(self, name: str, task: AgentTask, state: dict) -> AgentResult:
        agent = self.get(name)
        if agent is None:
            return AgentResult(name=name, success=False, error=f"未注册子 agent: {name}")
        return await agent.run(task, state)