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


    image_path: Optional[str]  # 当前处理的图片路径
    image_description: Optional[str]  # Vision API 对图片的描述
    image_processing_status: Literal[
        "idle",
        "waiting_for_user_intent",
        "waiting_for_skill_confirmation",
        "skill_executed"
    ]  # 图片处理进度
    image_available_skills: Optional[List[dict]]  # 推荐给用户的图片检测技能列表
