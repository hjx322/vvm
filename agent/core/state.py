"""状态定义模块"""

from typing import List, Optional, TypedDict, Literal, Union
from langchain_core.messages import AIMessage, HumanMessage, BaseMessage


class DigitalSmartDoctorState(TypedDict):
    """智能医生 Agent 的状态定义"""

    human_input: str  # 人类输入
    messages: List[Optional[BaseMessage]]  # 对话记录
    chat_name: Optional[str]  # 会话名称
    doctor_id: Optional[str]  # 医生智能体id
    user_id: Optional[str]  # 用户ID（租户标识）
    crm: str
    medical_record_no: Optional[str]  # 病历号
    patient_info: Optional[str]  # 患者基本信息
    patient_visit_record: Optional[str]  # 患者就诊记录
    patient_examine_result: Optional[str]  # 患者检查结果
    context_info: Optional[str]  # 额外的上下文信息
    final_answer: Optional[str]  # 最终回答
    sub_agent_input: str  # 子 Agent 的输入/输出

    # ===== 多 Agent 改造（单图内 supervisor-worker 编排）新增字段 =====
    worker_outputs: Optional[dict]  # L1/L2 worker 结果，key=worker 名（orchestrator 节点写入，随 checkpoint 持久化）
    worker_messages: Optional[dict]  # 推理 worker 独立记忆 {worker名: List[BaseMessage]}（多轮记忆，绝不清理）
    agent_plan: Optional[dict]  # SupervisorPlan 序列化（审计/可观测）
    final_answer_draft: Optional[str]  # L3 安全门卫前的回答草稿（瞬态）
    safety_verdict: Optional[dict]  # L3 结构化判定结果（审计）
    active_workers: Optional[list]  # 本轮实际被调起的 worker 名列表（orchestrator 写入，审计/评测用）
    worker_status: Optional[dict]  # 各 worker 执行状态 {worker名: success/failed}

    image_path: Optional[str]  # 当前处理的图片路径
    image_description: Optional[str]  # Vision API 对图片的描述
    image_processing_status: Literal[
        "idle",
        "waiting_for_user_intent",
        "waiting_for_skill_confirmation",
        "skill_executed"
    ]  # 图片处理进度
    image_available_skills: Optional[List[dict]]  # 推荐给用户的图片检测技能列表
    image_pending_skill: Optional[str]  # 待用户确认的图片技能名（低置信度反问时暂存，
                                        # 用户确认后直接复用，避免重复跑一次 LLM 意图识别）
