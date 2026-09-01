"""预处理节点：检测图片上传、解析用户输入"""

import os
from pathlib import Path
from agent.core.state import DigitalSmartDoctorState
from agent.utils.medical_record_extractor import extract_medical_record_no


class PreProcessNode:
    """预处理节点：提取病历号等预处理工作"""

    @staticmethod
    async def execute(state: DigitalSmartDoctorState) -> dict:
        """执行预处理逻辑

        Args:
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        update = {
            "medical_record_no": "",
            "user_id": "",
        }

        # 1. 提取病历号 和 用户ID
        #    优先使用前端显式透传的 medical_record_no（可命中混合编号如 yTATRIY-1ki），
        #    否则回退从 chat_name 正则提取纯数字串
        medical_record_no = state.get("medical_record_no") or ""
        if not medical_record_no and state.get("chat_name"):
            medical_record_no = extract_medical_record_no(state["chat_name"])
        if medical_record_no:
            update["medical_record_no"] = medical_record_no
            # user_id 与 medical_record_no 从同一来源提取，用于租户隔离
            update["user_id"] = medical_record_no


        # 2. 解析图片上传命令
        human_input = state.get("human_input", "").strip()
        if human_input.startswith("!image "):
            # 提取图片路径
            image_path = human_input[7:].strip()  # 去掉 "!image " 前缀

            if os.path.exists(image_path):
                update["image_path"] = image_path
                update["image_processing_status"] = "idle"
                update["image_description"] = None
                update["image_available_skills"] = []
            else:
                pass
        # 如果没有 !image 命令，保持原有的 image_path（用于多轮对话）

        if update.get("medical_record_no") !="":
            return update
        else:
            return state


