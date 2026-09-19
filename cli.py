"""数智医生智能体 - 交互式 CLI Shell

升级自 main.py：使用 prompt_toolkit 提供历史记录与按键编辑，
叠加 rich 提供彩色输出；流式显示 AI 回复。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel

from agent.core.constants import configure_logging
from agent.digital_smart_doctor_agent import DigitalSmartDoctorAgent

# 默认对话参数（可被 slashes 或环境覆盖）
DEFAULT_CRM = "hn"
DEFAULT_DOCTOR_ID = "agt_d75e25a434fa457f"
DEFAULT_CHAT_NAME = ""

HISTORY_FILE = Path(".cli_history")
# 支持的命令
COMMANDS = {
    "/exit": "退出对话",
    "/quit": "退出对话",
    "/reset": "重置当前会话（清空本轮记忆）",
    "/help": "显示帮助",
    "/crm <name>": "切换 CRM 数据库名（当前: {crm}）",
    "/doctor <id>": "切换医生智能体 id",
}

# AI 输出前缀（rich 标记）
AI_TAG = "[bold green]AI[/bold green]"


async def main() -> None:
    """交互式 shell 主流程"""
    configure_logging()

    workflow_id = str(uuid.uuid4())
    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        reserve_space_for_menu=3,
    )

    crm = DEFAULT_CRM
    doctor_id = DEFAULT_DOCTOR_ID
    chat_name = DEFAULT_CHAT_NAME

    console = Console()
    console.print(
        Panel.fit(
            "数智医生智能体 - 交互式 Shell\n"
            "输入 [bold]exit[/bold] / [bold]/quit[/bold] 退出，"
            "[bold]/reset[/bold] 重置会话，[bold]/help[/bold] 查看命令",
            title="🩺 智能医生",
        )
    )

    # 异步创建 Agent（连 MySQL + 初始化 LLM + 建图）
    agent = await DigitalSmartDoctorAgent.create(workflow_id=workflow_id)
    first_round = True  # 首轮 restart=True，用于恢复异常中断的会话

    try:
        while True:
            # 提示符（prompt_toolkit 默认带按键编辑与历史）
            try:
                user_input = await session.prompt_async("HUMAN > ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]对话结束[/dim]")
                break

            text = user_input.strip()
            if not text:
                continue

            # 处理内建命令
            if text.lower() in ("exit", "quit", "/exit", "/quit"):
                console.print("[dim]对话结束[/dim]")
                break
            if text.lower() in ("/help", "-h", "--help"):
                _show_help(console, crm)
                continue
            if text.lower() == "/reset":
                first_round = True
                console.print("[yellow]会话已重置，开启新一轮。[/yellow]")
                continue
            if text.lower().startswith(("/crm ", "/doctor ")):
                cmd, _, val = text.partition(" ")
                if val.strip():
                    if cmd == "/crm":
                        crm = val.strip()
                        console.print(f"[green]CRM 已切换为 {crm}[/green]")
                    else:
                        doctor_id = val.strip()
                        console.print(f"[green]医生 id 已切换为 {doctor_id}[/green]")
                else:
                    console.print("[yellow]用法: /crm <name> 或 /doctor <id>[/yellow]")
                continue

            # 流式调用 AI（aprocess 是 async def，先 await 拿到 async generator 再逐块输出）
            console.print(f"{AI_TAG} ", end="")
            try:
                stream = await agent.aprocess(
                    text, crm, chat_name, doctor_id,
                    response_type="stream", restart=first_round,
                )
                async for chunk in stream:
                    content = (
                        chunk.get("content", "")
                        if isinstance(chunk, dict)
                        else getattr(chunk, "content", "")
                    )
                    if content:
                        console.print(content, end="", soft_wrap=True)
                console.print()
            except Exception as e:  # noqa: BLE001
                logger.exception("对话处理失败")
                console.print(f"\n[bold red]错误:[/bold red] {e}")

            first_round = False
    finally:
        await agent.close()
        logger.info("agent 资源已释放")


def _show_help(console: Console, crm: str) -> None:
    """打印命令帮助"""
    console.print("[bold underline]可用命令[/bold underline]")
    for name, desc in COMMANDS.items():
        desc_fmt = desc.format(crm=crm)
        console.print(f"  [bold cyan]{name:<16}[/bold cyan] {desc_fmt}")


if __name__ == "__main__":
    asyncio.run(main())
