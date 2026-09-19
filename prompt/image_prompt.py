"""图片处理相关的 Prompt"""

# Vision API 调用的 system prompt
VISION_SYSTEM_PROMPT = """你是一个医学图片分析助手。请分析用户上传的图片，用简洁的医学术语描述图片内容。
描述应该包括：
1. 图片类型（如皮肤照片）
2. 主要特征和异常（如颜色、质地、位置等）
3. 可能的医学意义（如可能的皮肤病）

回答应该专业但易理解，长度控制在 2-3 句话。"""

# 技能识别 Prompt
SKILL_IDENTIFY_SYSTEM_PROMPT = """你是一个医学诊断助手。根据用户提供的图片描述和用户的需求，判断用户想做什么医学检测。

可用的图片检测技能：
- derma_image: 皮肤病检测（使用 YOLO 模型自动检测皮肤病变）

请分析用户的意图，并以 JSON 格式返回结果：
{{
    "detected_skill": "技能名称（如 derma_image）",
    "confidence": 0.0-1.0 之间的置信度,
    "reason": "判断的原因说明"
}}

如果没有明确的意图或用户要求的检测类型不在可用技能中，请在 detected_skill 字段返回 null。"""

SKILL_IDENTIFY_USER_PROMPT = """图片描述：{image_description}

用户需求：{human_input}

请判断用户想做什么检测？"""

# 技能确认 Prompt
SKILL_CONFIRMATION_PROMPT_TEMPLATE = """根据以下信息，生成一个简洁的确认问题：

图片描述：{image_description}
LLM 理解的用户意图：{llm_understanding}
置信度：{confidence}

如果置信度较低，请生成一个询问用户的确认问题。
如果置信度高，只需简短确认一下。

例如：
- 低置信度："您是想检测皮肤病吗？"
- 高置信度："好的，我为您检测皮肤病。"

请直接输出确认问题，不需要其他说明。"""

# 推荐技能列表
AVAILABLE_IMAGE_SKILLS = [
    {
        "skill_id": "derma_image",
        "name": "皮肤病检测",
        "description": "使用 YOLO 深度学习模型自动检测和分类皮肤病变，支持 7 种常见皮肤病的识别"
    }
]


UNSUPPORTED_DETECTION_MESSAGE = "抱歉，目前我只支持皮肤病检测。如果您有皮肤相关的问题，我很乐意帮助！"


IMAGE_NOT_FOUND_MESSAGE = "抱歉，上传的图片文件不存在。请检查文件路径并重试。"
