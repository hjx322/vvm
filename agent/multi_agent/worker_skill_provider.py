# -*- coding: utf-8 -*-
"""子 agent 技能配置提供器：从 DB 读某子 agent 启用的技能，替换硬编码 self.skills。

背景：多 agent 化后，子 agent 的技能从硬编码类属性改为"按 agent 从 MySQL 配置"。
本提供器按子 agent 名（patient_knowledge_agent 等）从 agent_skills 表查启用的技能，
运行时每轮调用 + 短期 TTL 缓存，使前端启停即时生效而无需重启。

租户口径：固定使用 configs.multi_agent.management_user_id（管理租户，如 1827196），
不从 state.user_id 取 —— 因为运行时 state.user_id 会被 pre_process 覆写为患者号。
"""

import time
from typing import Dict, List, Optional

from loguru import logger

from config.app_config import configs


class AgentSkillProvider:
    """按子 agent 名读取启用的技能（带 TTL 缓存的 DB 提供器）"""

    def __init__(
        self,
        tenant_user_id: Optional[str] = None,
        ttl_seconds: float = 10.0,
    ):
        self.tenant_user_id = tenant_user_id or getattr(
            configs.multi_agent, "management_user_id", "1827196"
        )
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple] = {}  # agent_name -> (fetch_time, List[str] | None)

    async def get_enabled_skill_names(self, agent_name: str) -> Optional[List[str]]:
        """返回该子 agent 启用的技能名列表。

        Returns:
            List[str]  : 查询成功（可能为空 = 该 agent 全禁用，一个技能都不执行）
            None       : 查询失败（DB 异常），调用方应降级回 agent 默认 skills

        带 TTL 缓存：ttl_seconds 内的同一 agent 直接返回缓存，避免每轮都查库。
        """
        now = time.monotonic()
        cached = self._cache.get(agent_name)
        if cached and (now - cached[0]) < self.ttl_seconds:
            return cached[1]

        try:
            # 惰性 import：避开后端服务栈的强依赖加载（仅首次调用时引入）
            from backend.database.session_factory import get_session_context
            from backend.services.worker_skill_manager import WorkerSkillManager

            with get_session_context() as db:
                manager = WorkerSkillManager(db)
                enabled = manager.get_enabled_skills(
                    user_id=self.tenant_user_id, agent_name=agent_name
                )
            names = [s["skill_id"] for s in enabled]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AgentSkillProvider] 查询 {agent_name} 技能失败，降级默认: {e}")
            self._cache[agent_name] = (now, None)
            return None

        self._cache[agent_name] = (now, names)
        if names:
            logger.debug(f"[AgentSkillProvider] {agent_name} 启用技能: {names}")
        return names


# 兼容别名：旧引用（历史脚本/探针）仍可用；新代码统一用 AgentSkillProvider
WorkerSkillProvider = AgentSkillProvider
