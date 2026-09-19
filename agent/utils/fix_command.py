# 命令修复
import os
import re
from agent.core.state import DigitalSmartDoctorState
def fix_llm_command(raw_command, skill_name,state: DigitalSmartDoctorState, base_skills_dir="./user_skills"):
    """
    智能修正大模型生成的技能执行命令。
    :param raw_command: 大模型生成的原始命令，例如 'python3 scripts/weather_helper.py current "New York"'
    :param skill_name: 当前确认要调用的技能名称，需要和文件夹名完全一致
    :param base_skills_dir: 技能库的根目录
    """
    match = re.search(r'([^\s"\'=]+\.(?:py|js|sh|bat))', raw_command)
    if not match:
        return raw_command 
        
    wrong_path = match.group(1)
    script_filename = os.path.basename(wrong_path) # 提取出纯文件名，如 'weather_helper.py'
    
    skill_dir = os.path.join(base_skills_dir,state['medical_record_no'], skill_name,'current')
    real_path = None
    
    if os.path.exists(skill_dir):
        for root, dirs, files in os.walk(skill_dir):
            if script_filename in files:

                real_path = os.path.join(root, script_filename)
                break
                
    if real_path:
        real_path = real_path.replace('\\', '/')
        return raw_command.replace(wrong_path, real_path)
    else:
        return raw_command

# === 测试用例 ===
if __name__ == "__main__":
    
    actual_skill_name = 'clawhub_weather-cn'
    
    cmd1 = './weather-cn.sh 上海'
    print(f"原命令: {cmd1}")
    print(f"修正后: {fix_llm_command(cmd1, actual_skill_name)}\n")
    

    cmd2 = 'curl -X GET "https://api.weather.com/v1/loc"'
    print(f"原命令: {cmd2}")
    print(f"修正后: {fix_llm_command(cmd2, actual_skill_name)}\n")