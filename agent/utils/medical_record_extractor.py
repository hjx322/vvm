"""病历号提取工具
进行医学记录的提取
"""

import re
from typing import Optional


def extract_medical_record_no(chat_name: Optional[str]) -> str:
    """从 chat_name 中提取病历号，返回最后一段数字串，不存在则返回空字符串
    
    Args:
        chat_name: 聊天名称，可能包含病历号
        
    Returns:
        提取的病历号字符串，未找到则返回空字符串
    """
    if not chat_name:
        return ""
    
    translation_table = str.maketrans("０１２３４５６７８９", "0123456789")
    normalized = str(chat_name).strip().translate(translation_table)
    numbers = re.findall(r"\d+", normalized)
    
    if not numbers:
        return ""
   
    max_len = max(len(item) for item in numbers)
    candidates = [item for item in numbers if len(item) == max_len]
    return candidates[-1]


