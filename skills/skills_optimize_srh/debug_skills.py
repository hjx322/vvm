# -*- coding: utf-8 -*-
"""调试技能列表"""
from prompt.skills_prompt import get_skills_description

# 测试参数
user_id = "1827196"  # medical_record_no
doctor_id = "agt_d75e25a434fa457f"

try:
    result = get_skills_description(user_id, doctor_id)
    print("===== 启用的技能列表 =====")
    print(result)
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
