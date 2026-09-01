import subprocess
import platform

def execute_agent_command(command_str, timeout_sec=30):
    """
    运行大模型生成的任意命令行指令，并返回结果。
    """
    # ==========================================
    # 步骤 1: Windows 环境的特殊“补丁”
    # ==========================================
    if platform.system() == "Windows":
        # 如果命令的第一个词是 .sh 脚本，强制给它套上 bash
        first_token = command_str.strip().split()[0]
        if first_token.endswith(".sh"):
            command_str = f"bash {command_str}"
            
        # 注意：有时候 Windows 下没有 python3 只有 python，你可以视你的环境决定是否要做替换
        # if command_str.startswith("python3 "):
        #     command_str = command_str.replace("python3 ", "python ", 1)

    print(f"⚙️ 正在执行子程序: {command_str}")

    # ==========================================
    # 步骤 2: 启动子进程并捕获结果
    # ==========================================
    try:
        # 这里的几个参数极其关键：
        result = subprocess.run(
            command_str,
            shell=True,            # 必须为 True：允许直接执行整串字符串，自动识别 npm、python 等系统环境变量
            capture_output=True,   # 捕获程序的 print() 或 echo 输出 (stdout 和 stderr)
            text=True,             # 将输出的字节流自动转为字符串
            encoding='utf-8',      # 强制使用 utf-8 编码，防止中文返回乱码
            errors='replace',      # 遇到极其生僻的字符无法解码时，用 ? 替换而不是让程序崩溃
            timeout=timeout_sec    # 【防卡死护城河】超过设定时间直接杀掉进程
        )
        
        # ==========================================
        # 步骤 3: 格式化返回值并喂给大模型
        # ==========================================
        if result.returncode == 0:
            # returncode 为 0 代表脚本平稳运行结束
            output = result.stdout.strip()
            return output if output else "✅ 命令执行成功，但没有返回任何文本输出。"
        else:
            # 脚本内部报错了（比如语法错误、缺少依赖）
            error_msg = result.stderr.strip()
            # 把错误信息返回给大模型，大模型看到错误往往能自己修正命令！
            return f"❌ 脚本执行失败 (Exit Code {result.returncode}):\n{error_msg}"
            
    except subprocess.TimeoutExpired:
        return f"⏳ 执行超时！命令执行超过了 {timeout_sec} 秒被系统强行终止。请检查脚本是否存在死循环或需要用户手动输入内容。"
    except Exception as e:
        return f"⚠️ 发生系统底层异常: {str(e)}"

# === 测试环节 ===
if __name__ == "__main__":
    res3 = execute_agent_command('./.claude/skills/clawhub_weather-cn/weather-cn.sh 西安')
    print(f"结果3:\n{res3}\n")