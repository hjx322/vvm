"""图片处理节点：
调用Vision API描述图片，识别用户意图(关键词匹配+LLM)，推荐相关技能
状态转移，技能关键词
"""

import json
import os
import base64
from typing import Dict, Optional, Tuple, List

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from loguru import logger
from openai import AsyncOpenAI
from config.app_config import configs

from agent.core.state import DigitalSmartDoctorState
from prompt.image_prompt import (
    VISION_SYSTEM_PROMPT,
    SKILL_IDENTIFY_SYSTEM_PROMPT,
    SKILL_IDENTIFY_USER_PROMPT,
    AVAILABLE_IMAGE_SKILLS,
    UNSUPPORTED_DETECTION_MESSAGE,
    IMAGE_NOT_FOUND_MESSAGE,
)


class ImageProcessNode:
    """图片处理节点：处理用户上传的图片，进行 Vision API 描述和技能选择"""


    SKILL_KEYWORDS = {
        "derma_image": [
            "皮肤病", "皮肤", "检测", "诊断", "皮肤诊断",
            "斑点", "痣", "痤疮", "湿疹", "癣",
            "黑色素瘤", "基底细胞癌", "鲍温病", "良性角化病",
            "皮肤纤维瘤", "黑素细胞痣", "血管病变"
        ]
    }

    def __init__(self):
        """初始化图片处理节点 - 使用 DashScope API"""
        self.client = AsyncOpenAI(
            api_key=configs.llm.dashscope.api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    @staticmethod
    def encode_image(image_path: str) -> str:
        """将图片编码为 Base64 字符串

        Args:
            image_path: 图片文件路径

        Returns:
            Base64 编码的图片字符串

        Raises:
            FileNotFoundError: 图片文件不存在
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    async def call_vision_api(self, image_path: str) -> str:
        """调用 Qwen Vision API 描述图片

        Args:
            image_path: 图片文件路径

        Returns:
            Vision API 的描述文本

        Raises:
            FileNotFoundError: 图片文件不存在
            Exception: Vision API 调用失败
        """
        try:
            base64_image = self.encode_image(image_path)
        except FileNotFoundError as e:
            logger.error(f"图片编码失败: {e}")
            raise

        try:
            response = await self.client.chat.completions.create(
                model="qwen-vl-max",  # 或 qwen-vl-plus
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                            },
                            {"type": "text", "text": VISION_SYSTEM_PROMPT}
                        ]
                    }
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Vision API 调用失败: {e}")
            raise

    def identify_skill_by_keyword(self, user_input: str) -> Tuple[Optional[str], float]:
        """尝试通过关键词识别用户想使用的技能

        Args:
            user_input: 用户输入

        Returns:
            (技能名称, 置信度) 元组，如果未找到则返回 (None, 0.0)
        """
        user_input_lower = user_input.lower()

        for skill_id, keywords in self.SKILL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in user_input_lower:
                    logger.info(f"通过关键词识别到技能: {skill_id}")
                    return skill_id, 1.0

        return None, 0.0

    async def identify_skill_by_llm(
        self, image_description: str, human_input: str
    ) -> Tuple[Optional[str], float]:
        """使用 LLM 识别用户想使用的技能

        Args:
            image_description: Vision API 的图片描述
            human_input: 用户输入

        Returns:
            (技能名称, 置信度) 元组，如果识别失败或不支持则返回 (None, 0.0)
        """
        try:
            response = await self.client.chat.completions.create(
                model="qwen-plus",
                messages=[
                    {
                        "role": "system",
                        "content": SKILL_IDENTIFY_SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": SKILL_IDENTIFY_USER_PROMPT.format(
                            image_description=image_description, human_input=human_input
                        )
                    }
                ]
            )

            response_text = response.choices[0].message.content

            # 尝试解析 JSON 响应
            try:
                result = json.loads(response_text)
                detected_skill = result.get("detected_skill")
                confidence = result.get("confidence", 0.0)

                if detected_skill:
                    logger.info(f"LLM 识别到技能: {detected_skill} (置信度: {confidence})")
                    return detected_skill, confidence
                else:
                    logger.info("LLM 未识别到有效的技能")
                    return None, 0.0
            except json.JSONDecodeError:
                logger.warning(f"LLM 返回无效的 JSON: {response_text}")
                return None, 0.0
        except Exception as e:
            logger.error(f"LLM 技能识别失败: {e}")
            return None, 0.0

    def check_skill_exists(self, skill_name: str) -> bool:
        """检查技能是否在可用列表中

        Args:
            skill_name: 技能名称

        Returns:
            技能是否存在
        """
        return any(s["skill_id"] == skill_name for s in AVAILABLE_IMAGE_SKILLS)

    async def execute(self, state: DigitalSmartDoctorState) -> dict:
        """执行图片处理节点的主逻辑 - 在一个节点内完成所有处理

        Args:
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        image_path = state.get("image_path")
        human_input = state.get("human_input", "").strip()
        image_processing_status = state.get("image_processing_status", "idle")

        if not image_path:
            return {}

        try:
            # ===== 场景1：新上传图片（idle 状态），无明确意图 =====
            if image_processing_status == "idle":
                logger.info("场景1：新上传图片，无明确意图")
                if human_input.startswith("!image"):
                    return await self._handle_image_only(image_path, state)
                else:
                    return await self._handle_image_with_description(image_path, human_input, state)

            # ===== 场景2：等待用户选择技能（waiting_for_user_intent 状态） =====
            elif image_processing_status == "waiting_for_user_intent":
                logger.info("场景2：用户已回答，处理技能选择")
                return await self._handle_user_skill_choice(image_path, human_input, state)

            # ===== 场景3：等待用户确认（waiting_for_skill_confirmation 状态） =====
            elif image_processing_status == "waiting_for_skill_confirmation":
                logger.info("场景3：用户确认选择，执行技能")
                return await self._handle_skill_confirmation(image_path, human_input, state)

            return {}

        except FileNotFoundError:
            logger.error(f"图片文件不存在: {image_path}")
            return {
                "image_path": None,
                "image_processing_status": "idle",
                "messages": state.get("messages", []) + [
                    AIMessage(content=IMAGE_NOT_FOUND_MESSAGE)
                ]
            }
        except Exception as e:
            logger.exception(f"图片处理异常: {e}")
            return {
                "image_path": None,
                "image_processing_status": "idle",
                "messages": state.get("messages", []) + [
                    AIMessage(content=f"处理图片时出错: {str(e)}")
                ]
            }

    async def _handle_image_only(self, image_path: str, state: DigitalSmartDoctorState) -> dict:
        """场景1：只上传图片，无明确意图

        Args:
            image_path: 图片路径
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        logger.info(f"调用 Vision API 描述图片: {image_path}")
        image_description = await self.call_vision_api(image_path)

        skills_list = "\n".join(
            f"{i+1}. {skill['name']} - {skill['description']}"
            for i, skill in enumerate(AVAILABLE_IMAGE_SKILLS)
        )

        message = f"""识别结果如下：
📸 图片描述：
{image_description}

我们支持以下检测：
{skills_list}

请告诉我您想对这张图片做什么？"""
        print(message)

        new_messages = state.get("messages", []) + [AIMessage(content=message)]

        return {
            "image_path": image_path,
            "image_description": message,
            "image_processing_status": "waiting_for_user_intent",
            "image_available_skills": AVAILABLE_IMAGE_SKILLS,
            "messages": new_messages,
        }

    async def _handle_image_with_description(
        self, image_path: str, human_input: str, state: DigitalSmartDoctorState
    ) -> dict:
        """场景2：上传图片+用户描述

        Args:
            image_path: 图片路径
            human_input: 用户输入
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        # Step 1: 调用 Vision API 描述图片
        logger.info(f"调用 Vision API: {image_path}")
        image_description = await self.call_vision_api(image_path)

        # Step 2: 尝试通过关键词识别技能
        skill_name, confidence = self.identify_skill_by_keyword(human_input)

        # Step 3: 如果关键词识别失败，尝试 LLM 识别
        if not skill_name:
            logger.info("关键词识别失败，尝试 LLM 识别")
            skill_name, confidence = await self.identify_skill_by_llm(image_description, human_input)

        # Step 4: 检查技能是否存在
        if not skill_name or not self.check_skill_exists(skill_name):
            logger.warning(f"技能不存在或不支持: {skill_name}")
            return {
                "image_path": None,
                "image_processing_status": "idle",
                "messages": state.get("messages", []) + [
                    AIMessage(content=UNSUPPORTED_DETECTION_MESSAGE)
                ]
            }

        # Step 5: 根据置信度决定是否需要确认
        if confidence >= 0.8:
            logger.info(f"高置信度识别: {skill_name} ({confidence})")
            # 直接返回技能执行，不需要确认
            return await self._prepare_skill_execution(skill_name, image_path, image_description, state)
        else:
            logger.info(f"低置信度识别: {skill_name} ({confidence})，需要用户确认")
            # 需要用户确认
            return await self._prepare_skill_confirmation(skill_name, image_path, image_description, confidence, state)

    async def _prepare_skill_execution(
        self, skill_name: str, image_path: str, image_description: str, state: DigitalSmartDoctorState
    ) -> dict:
        """准备执行技能

        Args:
            skill_name: 技能名称
            image_path: 图片路径
            image_description: 图片描述
            state: 当前状态

        Returns:
            更新后的状态字典（包含 sub_agent_input，用于 SkillQueryNode 执行）
        """
        logger.info(f"准备执行技能: {skill_name}")

        # 格式化技能执行的提示
        message = f"""好的，我为您执行{self._get_skill_name(skill_name)}。

📸 图片分析：{image_description}

正在处理..."""

        new_messages = state.get("messages", []) + [AIMessage(content=message)]

        # 将图片路径传递给 SkillQueryNode，让它调用 derma_image skill
        # 设置 sub_agent_input 为包含图片路径的 JSON，SkillQueryNode 会提取使用
        skill_input = json.dumps({
            "skill_name": skill_name,
            "image_path": image_path,
            "model_name": "YOLOv11.pt"  # 默认模型
        })

        return {
            "image_path": None,  # 清除图片路径，技能执行后回到普通流程
            "image_description": None,
            "image_processing_status": "idle",
            "image_available_skills": [],
            "messages": new_messages,
            "sub_agent_input": skill_input,  # 传递给 SkillQueryNode
        }

    async def _prepare_skill_confirmation(
        self, skill_name: str, image_path: str, image_description: str, confidence: float, state: DigitalSmartDoctorState
    ) -> dict:
        """准备技能确认（用户需要确认）

        Args:
            skill_name: 技能名称
            image_path: 图片路径
            image_description: 图片描述
            confidence: 置信度
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        logger.info(f"准备技能确认: {skill_name} (置信度: {confidence})")

        skill_name_cn = self._get_skill_name(skill_name)
        message = f"""根据图片和您的描述，我认为您想进行{skill_name_cn}。

📸 图片分析：{image_description}

这个判断准确吗？"""

        new_messages = state.get("messages", []) + [AIMessage(content=message)]

        return {
            "image_path": image_path,  # 保留图片路径
            "image_description": image_description,
            "image_processing_status": "waiting_for_skill_confirmation",
            "messages": new_messages,
        }

    @staticmethod
    def _get_skill_name(skill_id: str) -> str:
        """获取技能的中文名称

        Args:
            skill_id: 技能 ID

        Returns:
            技能中文名称
        """
        skill_map = {
            "derma_image": "皮肤病检测"
        }
        return skill_map.get(skill_id, skill_id)

    async def _handle_user_skill_choice(
        self, image_path: str, human_input: str, state: DigitalSmartDoctorState
    ) -> dict:
        """场景2：用户在 waiting_for_user_intent 状态下回答，处理技能选择

        Args:
            image_path: 图片路径
            human_input: 用户输入（用户的选择）
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        logger.info(f"处理用户技能选择: {human_input}")

        image_description = state.get("image_description", "")

        skill_name, confidence = self.identify_skill_by_keyword(human_input)

        if not skill_name:
            logger.info("关键词识别失败，尝试 LLM 识别")
            skill_name, confidence = await self.identify_skill_by_llm(image_description, human_input)

        if not skill_name or not self.check_skill_exists(skill_name):
            logger.warning(f"技能不存在或不支持: {skill_name}")
            return {
                "image_path": None,  # 清除图片路径
                "image_processing_status": "idle",
                "messages": state.get("messages", []) + [
                    AIMessage(content=UNSUPPORTED_DETECTION_MESSAGE)
                ]
            }

        # 根据置信度决定是否需要确认
        if confidence >= 0.8:
            logger.info(f"高置信度识别: {skill_name} ({confidence})")
            return await self._prepare_skill_execution(skill_name, image_path, image_description, state)
        else:
            logger.info(f"低置信度识别: {skill_name} ({confidence})，需要用户确认")
            return await self._prepare_skill_confirmation(skill_name, image_path, image_description, confidence, state)

    async def _handle_skill_confirmation(
        self, image_path: str, human_input: str, state: DigitalSmartDoctorState
    ) -> dict:
        """场景3：用户在 waiting_for_skill_confirmation 状态下回答，处理确认

        Args:
            image_path: 图片路径
            human_input: 用户输入（是/否等确认）
            state: 当前状态

        Returns:
            更新后的状态字典
        """
        logger.info(f"处理用户确认: {human_input}")

        confirmation_words = ["是", "对", "可以", "好", "好的", "行", "同意"]
        human_input_lower = human_input.lower()

        confirmed = any(word in human_input_lower for word in confirmation_words)

        if confirmed:
            # 用户确认，获取之前推荐的技能
            image_description = state.get("image_description", "")

            # 重新用 LLM 识别（因为之前已经识别过，这次应该相同）
            skill_name, confidence = await self.identify_skill_by_llm(
                image_description,
                state.get("messages", [])[-1].content if state.get("messages") else ""
            )

            if skill_name and self.check_skill_exists(skill_name):
                logger.info(f"用户确认技能: {skill_name}")
                return await self._prepare_skill_execution(skill_name, image_path, image_description, state)
            else:
                # 识别失败，返回错误
                return {
                    "image_path": None, 
                    "image_processing_status": "idle",
                    "messages": state.get("messages", []) + [
                        AIMessage(content="抱歉，处理出错了。请重新上传图片。")
                    ]
                }
        else:
            # 用户不确认，回到等待用户选择技能的状态
            return {
                "image_path": image_path,  
                "image_description": state.get("image_description", ""),
                "image_processing_status": "waiting_for_user_intent", 
                "messages": state.get("messages", []) + [
                    AIMessage(content="好的，请告诉我您想做什么检测。")
                ]
            }
