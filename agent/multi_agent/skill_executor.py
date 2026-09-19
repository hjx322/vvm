"""技能执行壳（薄壳，计划 P2）

不重写 DB / Milvus / 沙箱，直接复用 UnifiedSkillDispatcher 内核：
  - _execute_single_skill（skill_dispatcher.py:447，带超时/路由/降级）
  - _process_results   （skill_dispatcher.py:519，成功/失败聚合）
子 agent 只声明"我要哪几个技能"，执行细节全部下沉给现有调度器。
"""

import asyncio
from typing import List

from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher


class SkillExecutor:
    """技能执行薄壳"""

    def __init__(self, dispatcher: UnifiedSkillDispatcher):
        self.dispatcher = dispatcher

    async def execute_skills(self, skill_names: List[str], state: dict, human_input: str) -> dict:
        """并行执行一组技能并聚合

        Args:
            skill_names: 技能名列表（mysql_query/milvus_query/web_search/derma_image…）
            state: 当前 Agent 状态（含 medical_record_no/crm/user_id 等）
            human_input: 用户输入（作为技能参数来源）

        Returns:
            {"successful": [格式化结果…], "failed": [失败说明…], "skipped": bool}

        skipped=True 表示"本轮没有技能要执行"（被前置条件剪枝剪空），
        与"执行了但失败"（failed 非空）是两回事，编排层不应报为异常。
        """
        # 前置条件剪枝：无真实图片时不执行 derma_image 等依赖图片的技能。
        # 单 agent 路径在 dispatch():766 已有此过滤，multi-agent 此前缺失；
        # 池作用域下沉到 match 后，Tier2/FTS 的弱命中会更直接暴露（如无图却跑 derma_image）。
        skill_names = self.dispatcher._prune_skills_by_prerequisites(
            skill_names, state, human_input
        )
        if not skill_names:
            return {"successful": [], "failed": [], "skipped": True}

        tasks = [
            self.dispatcher._execute_single_skill(name, state, human_input)
            for name in skill_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful, failed = self.dispatcher._process_results(results, skill_names)
        return {"successful": list(successful), "failed": list(failed), "skipped": False}

    async def select_and_execute(
        self,
        skill_pool: List[str],
        human_input: str,
        state: dict,
        agent_prompt: str = "",
    ) -> dict:
        """三级技能选择 + 执行（子 agent 执行技能前调用）：先对技能池做三级选择，
        再并行执行命中子集；选择为空时返回空结果。

        Args:
            skill_pool: 该子 agent 的技能池（DB skill_provider or 默认 self.skills）
            human_input: 用户输入（作为技能参数来源）
            state: 当前 Agent 状态
            agent_prompt: 该子 agent 的 system_prompt（注入 Tier3 LLM 选择的身份/职责）

        Returns:
            {"successful": [格式化结果…], "failed": [失败说明…], "skipped": bool}
        """
        selected = await self.dispatcher.select_skills_in_pool(
            skill_pool, human_input, state, agent_prompt=agent_prompt
        )
        if not selected:
            # 三级选择判定"本轮无需技能"（或池为空）→ 跳过执行。
            # 这是正常结局而非失败：调用方据此不要把该 agent 报成"结果异常"。
            return {"successful": [], "failed": [], "skipped": True}
        return await self.execute_skills(selected, state, human_input)