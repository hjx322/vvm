"""常量定义模块"""

import logging

# Graph 中断节点配置
INTERRUPT_NODES = ["node_empty"]

# 日志配置
def configure_logging():
    """配置日志级别，屏蔽第三方库的 INFO 日志"""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
