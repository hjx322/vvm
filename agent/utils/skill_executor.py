"""Skill 执行器 - 提取公共逻辑"""

import re
from typing import List
from loguru import logger


def extract_skill_names(model_output: str) -> List[str]:
    """从模型输出中提取所有skill名称，支持多个skill调用

    Args:
        model_output: LLM的输出内容

    Returns:
        提取到的skill名称列表（已去重）

    Raises:
        ValueError: 如果没有找到有效的skill调用模式
    """
    pattern = r'(?i)\bopenskills\s+read\s+([a-zA-Z0-9_\-]+)\b'
    matches = re.findall(pattern, model_output.strip())

    if not matches:
        raise ValueError("No valid 'openskills read <skill>' pattern found in model output.")

    # 验证每个skill名称的格式
    valid_skills = []
    for skill_name in matches:
        skill_name = skill_name.strip()
        if not skill_name:
            continue
        if not re.fullmatch(r'[a-zA-Z0-9_\-]+', skill_name):
            logger.warning(f"Invalid skill name format skipped: {skill_name}")
            continue
        valid_skills.append(skill_name)

    if not valid_skills:
        raise ValueError("No valid skill names extracted.")

    # 去重但保持顺序
    seen = set()
    unique_skills = []
    for skill in valid_skills:
        if skill not in seen:
            seen.add(skill)
            unique_skills.append(skill)

    return unique_skills


class SkillExecutor:
    """Skill 执行器基类"""
    pass
