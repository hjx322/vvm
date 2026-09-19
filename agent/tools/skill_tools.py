"""skill技能调用工具，外部 Skill 工具定义 (v2 — 增强沙箱)

v2 改进（借鉴 QClaw 龙虾管家安全防护）：
  - 租户隔离：自定义技能 cwd 限制在 user_skills/{user_id}/ 内
  - 环境变量最小化：只保留 PATH，防止信息泄露
  - 路径穿越三级校验
  - 资源限制：独立超时 + 输出截断

提供两个 LangChain Tool 供 LLM 工具循环按需调用：
  - load_skill_resource：读取技能目录下的文档文件（.md/.json/.txt）
  - execute_skill_script：执行技能目录下的脚本文件（.sh/.py）

使用 make_skill_tools(base_dir, user_id) 工厂函数创建绑定了根目录和租户的工具实例。
"""

import os
import platform
import subprocess
from typing import Dict, List, Optional

from langchain_core.tools import tool
from loguru import logger


# 最大允许的输出字节数（防止大量数据塞入 LLM 上下文）
MAX_OUTPUT_BYTES = 128 * 1024  # 128 KB


def _resolve_safe_path(
    base_dir: str, relative_path: str, sandbox_root: Optional[str] = None
) -> Optional[str]:
    """将相对路径解析为绝对路径，并校验是否在 base_dir 内（防路径穿越）。

    三级校验：
    1. realpath 规范化
    2. 必须位于 base_dir 子目录内
    3. 必须位于 sandbox_root 子目录内（租户隔离）

    Args:
        base_dir: 技能根目录的绝对路径
        relative_path: 相对于技能根目录的路径
        sandbox_root: 租户沙箱根目录（如 user_skills/{user_id}/），None 表示不限制

    Returns:
        安全的绝对路径；若路径越界则返回 None
    """
    base_dir = os.path.realpath(base_dir)
    full_path = os.path.realpath(os.path.join(base_dir, relative_path))

    # 校验 1：必须在 base_dir 内
    if not full_path.startswith(base_dir + os.sep) and full_path != base_dir:
        logger.warning(f"[Sandbox] 路径穿越拦截 (base_dir): {relative_path} -> {full_path}")
        return None

    # 校验 2：必须在 sandbox_root 内（租户隔离）
    if sandbox_root:
        sandbox_root = os.path.realpath(sandbox_root)
        if not full_path.startswith(sandbox_root + os.sep) and full_path != sandbox_root:
            logger.warning(f"[Sandbox] 路径穿越拦截 (sandbox): {relative_path} -> {full_path}")
            return None

    return full_path


def _build_sandbox_env() -> dict:
    """构建最小化环境变量（防信息泄露）

    只保留 PATH 和必要的系统变量，避免脚本通过环境变量读取敏感信息。
    """
    safe_env = {}
    # 只传递必要变量
    for key in ("PATH", "SystemRoot", "TEMP", "TMP", "HOME", "USER"):
        if key in os.environ:
            safe_env[key] = os.environ[key]

    # Windows 需要这些
    if platform.system() == "Windows":
        for key in ("COMSPEC", "PATHEXT", "WINDIR", "SYSTEMROOT"):
            if key in os.environ:
                safe_env[key] = os.environ[key]

    # Python 相关
    for key in ("PYTHONPATH", "PYTHONUNBUFFERED"):
        if key in os.environ:
            safe_env[key] = os.environ[key]

    # UTF-8 编码
    safe_env["PYTHONIOENCODING"] = "utf-8"
    safe_env["LANG"] = os.environ.get("LANG", "en_US.UTF-8")

    return safe_env


def make_skill_tools(base_dir: str, user_id: str = ""):
    """工厂函数：创建绑定了技能根目录和租户沙箱的工具实例。

    Args:
        base_dir: 技能的 current 目录绝对路径
        user_id: 用户 ID（用于租户沙箱隔离）

    Returns:
        [load_skill_resource_tool, execute_skill_script_tool] 工具列表
    """
    abs_base = os.path.realpath(base_dir)

    # 计算沙箱根目录（租户隔离边界）
    sandbox_root = None
    if user_id:
        sandbox_root = os.path.realpath(os.path.join("user_skills", user_id))

    sandbox_env = _build_sandbox_env()

    @tool
    def load_skill_resource(resource_path: str) -> str:
        """读取技能目录下的文档文件（.md / .json / .txt）。
        当技能指令（SKILL.md）要求阅读 references/ 或 agents/ 中的某文件时调用。

        Args:
            resource_path: 相对于技能 current 目录的路径，
                           如 agents/coach_alex.md 或 references/male_training.md

        Returns:
            文件内容字符串；若文件不存在或路径越界则返回错误提示。
        """
        safe_path = _resolve_safe_path(abs_base, resource_path, sandbox_root)
        if safe_path is None:
            return f"[错误] 非法路径或权限不足: {resource_path}"
        if not os.path.exists(safe_path):
            return f"[错误] 文件不存在: {resource_path}"

        allowed_ext = {".md", ".json", ".txt", ".yaml", ".yml"}
        ext = os.path.splitext(safe_path)[1].lower()
        if ext not in allowed_ext:
            return f"[错误] 不支持读取该类型文件: {ext}（仅支持 {allowed_ext}）"

        try:
            with open(safe_path, "r", encoding="utf-8") as f:
                content = f.read(MAX_OUTPUT_BYTES)
            logger.debug(f"load_skill_resource: 读取成功 {resource_path} ({len(content)} 字节)")
            if len(content) >= MAX_OUTPUT_BYTES:
                content += "\n\n[已截断，文件超过 128KB]"
            return content
        except Exception as e:
            logger.error(f"load_skill_resource: 读取失败 {resource_path}: {e}")
            return f"[错误] 读取文件失败: {e}"

    @tool
    def execute_skill_script(script_path: str, script_args: str = "") -> str:
        """执行技能目录下的脚本文件（.sh / .py）。
        当技能指令（SKILL.md）要求执行某个脚本并获取其输出时调用。

        Args:
            script_path: 相对于技能 current 目录的脚本路径，如 weather-cn.sh
            script_args: 传递给脚本的命令行参数字符串，如 "北京" 或 "--city 上海"

        Returns:
            脚本的标准输出内容；若执行失败则返回错误信息。
        """
        safe_path = _resolve_safe_path(abs_base, script_path, sandbox_root)
        if safe_path is None:
            return f"[Sandbox] 非法路径或权限不足: {script_path}"
        if not os.path.exists(safe_path):
            return f"[Sandbox] 脚本不存在: {script_path}"

        allowed_ext = {".sh", ".py"}
        ext = os.path.splitext(safe_path)[1].lower()
        if ext not in allowed_ext:
            return f"[Sandbox] 不支持执行该类型文件: {ext}（仅支持 {allowed_ext}）"

        # 构建命令（使用安全路径）
        if ext == ".sh":
            if platform.system() == "Windows":
                cmd = f'bash "{safe_path}" {script_args}'.strip()
            else:
                cmd = f'sh "{safe_path}" {script_args}'.strip()
        else:  # .py
            cmd = f'python "{safe_path}" {script_args}'.strip()

        logger.info(f"[Sandbox] 执行: {cmd[:120]}...")
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                cwd=abs_base,
                env=sandbox_env,  # v2: 最小化环境变量
            )
            if result.returncode == 0:
                output = result.stdout.strip()[:MAX_OUTPUT_BYTES]
                logger.debug(f"[Sandbox] 执行成功，输出 {len(output)} 字节")
                return output if output else "脚本执行成功，无文本输出。"
            else:
                error = result.stderr.strip()[:4096]
                logger.warning(f"[Sandbox] 执行失败 (code={result.returncode}) {error}")
                return f"[Sandbox] 脚本执行失败 (Exit Code {result.returncode}):\n{error}"
        except subprocess.TimeoutExpired:
            return "[Sandbox] 脚本执行超时（300秒）"
        except Exception as e:
            logger.exception(f"[Sandbox] 执行异常")
            return f"[Sandbox] 执行异常: {e}"

    return [load_skill_resource, execute_skill_script]
