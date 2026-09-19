"""具体子 agent 实现（计划 P2 → agent 化统一命名）

L1 数据 agent：patient_knowledge（原 patient+knowledge 合并）/ search / imaging / general，
全部 keep_history=False（可重放取数器，历史会误导）；reasoning 类（safety）在 reasoning_workers.py。
build_all_agents() 按 application.yaml multi_agent.agents 过滤注册（原 workers 段由死配置转为真生效）。
"""

from typing import List

from loguru import logger

from agent.multi_agent.base_worker import BaseAgent
from agent.multi_agent.schemas import AgentResult, AgentTask


class PatientKnowledgeAgent(BaseAgent):
    """L1 · 患者数据 + 医学知识 agent（原 patient_worker + knowledge_worker 合并 → worker 概念统一为 agent）

    单 agent 双通道：
      - mysql_query（always_skills）：无条件执行、每轮必查患者病历（保住原 force_patient 语义）；
      - milvus_query（可池技能）：按 query 三级选择后再检索疾病/药物/护理医学知识。
    run() 收到 force_patient payload（如天气轮 orchestrator 强制补查病歷时）只跑无条件技能。
    """

    role: str = "worker"  # 模型档位键（保留，勿改：对应 llm.roles.worker）
    name: str = "patient_knowledge_agent"
    description: str = "查询患者档案/就诊记录/检查结果（mysql）+ 检索疾病/药物/护理医学知识（milvus）"
    skills: List[str] = ["mysql_query", "milvus_query"]
    always_skills: List[str] = ["mysql_query"]
    # 局部上下文投影：agent 代码层只见职责字段（human_input/病历号/crm…），避免全局 state 污染
    context_fields: List[str] = ["human_input", "user_id", "medical_record_no", "crm"]
    keep_history: bool = False
    # 独立 system prompt：仅针对"患者档案 + 医学知识检索"这一子任务（提示词单一）
    system_prompt: str = (
        "你是 patient_knowledge_agent，只负责按病历号查询患者档案/就诊/检查结果"
        "（mysql_query 每轮必跑）并按需检索医学知识库（milvus_query）；"
        "不处理联网实时或图像任务。"
    )

    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        # force 轮（orchestrator 强制补查病歷）只跑无条件技能，跳过可池的 milvus_query
        force_patient = bool(task.payload.get("force_patient"))
        return await self._exec_skills(
            task, state, self.skills, run_selectable=not force_patient
        )


class SearchAgent(BaseAgent):
    """L1 · 联网 agent：最新信息检索（web_search / Tavily）"""

    role: str = "worker"
    name: str = "search_agent"
    description: str = "互联网联网检索最新信息"
    skills: List[str] = ["web_search"]
    keep_history: bool = False
    system_prompt: str = (
        "你是 search_agent，只负责互联网/实时信息检索（web_search 或天气等通用技能）；"
        "不查询患者病历、不做图像识别。"
    )

    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        return await self._exec_skills(task, state, self.skills)


class ImagingAgent(BaseAgent):
    """L1 · 皮肤图像 agent：识别上传皮肤照片（derma_image / YOLO）

    需要 state 中存在真实图片路径（image_path 或 sub_agent_input 里的 image_path），
    否则 dispatcher 的 _prune_skills_by_prerequisites 会剪掉 derma_image。
    """

    role: str = "worker"
    name: str = "imaging_agent"
    description: str = "皮肤图像识别（用户上传皮肤照片/描述皮肤病症状时）"
    skills: List[str] = ["derma_image"]
    keep_history: bool = False
    system_prompt: str = (
        "你是 imaging_agent，只在用户提供皮肤照片时执行 derma_image 皮肤检测；"
        "无图片时不执行。"
    )

    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        return await self._exec_skills(task, state, self.skills)


class GeneralAgent(BaseAgent):
    """L1 · 通用助手：寒暄/常识/资讯/天气/休闲等非医疗或混合问题的承接（可联网/通用技能）"""

    role: str = "worker"
    name: str = "general_agent"
    description: str = "通用助手：寒暄/常识/资讯/天气/娱乐等非医疗或混合问题（联网检索或通用技能）"
    skills: List[str] = ["web_search"]  # DB provider 可追加 weather 等通用技能
    keep_history: bool = False
    system_prompt: str = (
        "你是通用智能助手 general_agent，只承接闲聊/常识/资讯/娱乐等非医疗或混合问题，"
        "不要查询患者病历；优先选择通用/联网技能取数，无可用技能时简洁自然作答。"
    )

    async def run(self, task: AgentTask, state: dict) -> AgentResult:
        return await self._exec_skills(task, state, self.skills)


def _filter_enabled_agents(agents: List[BaseAgent]) -> List[BaseAgent]:
    """按 application.yaml multi_agent.agents 过滤注册；缺省 key = 启用（保守兼容现状）。

    背景：原 yaml 的 workers/agents 段曾是死配置；现真正控制注册。
    缺省 key = 启用：若默认禁用，always_query_patient 可能因 patient_knowledge_agent
    未注册而丢失病历查询，医疗场景不可接受。显式写 false 才关闭。
    """
    from config.app_config import configs

    cfg: dict = getattr(configs.multi_agent, "agents", None) or {}
    out: List[BaseAgent] = []
    for a in agents:
        if cfg.get(a.name, True):  # 缺省启用：未配置 key 不改变现状
            out.append(a)
        else:
            logger.info(f"[MultiAgent] {a.name} 已由 application.yaml 关闭，注册被跳过")
    return out


def build_data_agents(
    llm=None,
    dispatcher=None,
) -> List[BaseAgent]:
    """L1 数据 agent 集合（全部 keep_history=False，可并行 fan-out）"""
    return [
        PatientKnowledgeAgent(llm=llm, dispatcher=dispatcher),
        SearchAgent(llm=llm, dispatcher=dispatcher),
    ]


def build_all_agents(
    dispatcher=None,
    llm=None,
) -> List[BaseAgent]:
    """全部子 agent：L1 数据（含 imaging/general）+ L2/L3 reasoning，按 yaml agents 段过滤。"""
    from agent.multi_agent.reasoning_workers import build_reasoning_agents

    return _filter_enabled_agents(
        build_data_agents(dispatcher=dispatcher, llm=llm)
        + build_reasoning_agents(dispatcher=dispatcher, llm=llm)
        + [
            ImagingAgent(llm=llm, dispatcher=dispatcher),
            GeneralAgent(llm=llm, dispatcher=dispatcher),
        ]
    )


# 兼容别名：旧调用方（外部/历史脚本）沿用 build_all_workers 仍可用；新代码统一用 build_all_agents
build_all_workers = build_all_agents