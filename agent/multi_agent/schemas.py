"""多 Agent 结构化协议（计划 P2）

SupervisorPlan 供 Supervisor 的 with_structured_output 使用（同
skill_dispatcher.py:38-52 的 SkillSelectionResult 已验证链路）；
AgentTask / AgentResult 是子 agent 间传参的轻量协议。
SafetyVerdict 是 L3 用药安全门卫的结构化判定（替代脆弱的字符串匹配）。
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class AgentTask(BaseModel):
    """单个子 agent 的执行任务"""

    name: str = Field(description="目标子 agent 名（如 patient_knowledge_agent）")
    human_input: str = Field("", description="要处理的用户输入")
    skill_names: List[str] = Field(default_factory=list, description="该 worker 依赖的技能（mysql_query/milvus_query/web_search…）")
    payload: Dict = Field(default_factory=dict, description="额外参数")


class AgentResult(BaseModel):
    """单个子 agent 的执行结果"""

    name: str = Field(description="子 agent 名")
    success: bool = Field(False, description="是否成功")
    content: str = Field("", description="结果文本")
    skipped: bool = Field(
        False,
        description="本轮无技能可执行而跳过（LLM 判定无需技能 / 池为空 / 被前置条件剪枝）——"
        "区别于 success=False 的『执行失败』，编排层不应报为结果异常",
    )
    error: Optional[str] = Field(None, description="失败原因")
    duration: float = Field(0.0, description="执行耗时(秒)")
    history_update: Optional[Dict[str, Any]] = Field(
        None,
        description="reasoning agent 追加独立历史后的 state 更新片段（{worker_messages:…}）",
    )


class AgentRef(BaseModel):
    """容错：LLM 可能把单个子 agent 输出成对象 {name, skills…} 而非纯字符串。

    用此模型只取 name 字段，忽略其他杂散字段（如模型误带的 skills），
    保证 `SupervisorPlan.workers` 即便收到对象也能正常解析。
    """

    name: str = ""


class SupervisorPlan(BaseModel):
    """Supervisor 规划的结构化输出

    needs_workers=False 时本轮直接走综合（direct_reply 可选地给出
    无需子 agent 的回复要点），跳过所有 agent——这就是"闲聊直达"的降级开关。
    """

    needs_workers: bool = Field(True, description="本轮是否需要唤起子 agent（纯闲聊/问候时为 False）")
    # 允许"字符串 agent 名"或"对象 {name}"，由 worker_names 归一成字符串列表，
    # 兼容 LLM 偶尔把技能等杂散字段塞进 agent 对象（见 AgentRef）。
    workers: List[Union[str, AgentRef]] = Field(
        default_factory=list,  # 字段名保留（内部协议 + 旧 checkpoint 兼容），值为 *_agent
        description="本轮唤起的子 agent 名：patient_knowledge_agent/search_agent/imaging_agent/general_agent",
    )
    needs_medication_safety: bool = Field(False, description="本轮涉及用药，需唤起 safety_worker 复核")
    task_summary: str = Field("", description="对用户问题的任务拆解摘要（供 supervisor 综合参考）")
    direct_reply: Optional[str] = Field(
        None,
        description="needs_workers=False 时的直接回复要点（由 supervisor 综合生成完整话术）",
    )

    @property
    def worker_names(self) -> List[str]:
        """把 workers（纯字符串或对象）统一归一到子 agent 名字符串列表，供编排使用
        （属性名保留：内部协议 + 旧 checkpoint plan dict 兼容）。"""
        names = []
        for w in self.workers:
            if isinstance(w, str):
                names.append(w)
            else:
                names.append(w.name)
        return [n for n in names if n]


class SafetyVerdict(BaseModel):
    """L3 用药安全门卫的结构化判定结果"""

    blocked: bool = Field(False, description="是否需要附加免责/就医提醒（不再拦截回答本身）")
    violations: List[str] = Field(default_factory=list, description="触发免责的点：具体药名/剂量/开药表述/急症信号等")
    reason: str = Field("", description="判定理由")