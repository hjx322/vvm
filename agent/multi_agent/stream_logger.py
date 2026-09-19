# -*- coding: utf-8 -*-
"""把 loguru 日志桥接到前端思考过程（type=thought 流）

多 agent 流式对话期间，后端任意模块用 loguru 打的 INFO 日志（如
"MySQL 连接成功建立"、web_search 查到的每条结果、milvus 检索摘要等）
被挂在 loguru 根 logger 上的 sink 捕获，并转发给"当前活动的
LangGraph stream_writer"，作为思考块显示在前端。

- 仅转发 INFO 及以上级别（loguru level="INFO"，不含 DEBUG 调试噪音）。
- format="{message}" 只保留日志内容，去掉 loguru 的时间戳/级别/调用位置前缀
  （即用户所称的 "2026-09-02 17:34:34.396 | INFO | agent.xxx:70 -"）。
- sink 只在对话节点执行期间由 orchestrator start/stop，非对话（CLI/后台）
  不转发，避免污染无关输出。

用法（在 orchestrator 编排节点内）：
    writer = self._stream()
    with stream_logger.bridge(writer):
        # 这里任意模块打的 INFO 日志都会流式显示到前端
        ...
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from loguru import logger

# 按"对话协程"隔离的活动 writer（ContextVar：桥接会在 execute 协程里 set，
# 其下的子 agent 子协程（asyncio.gather）自动继承当前 context）——
# 这样多个并发对话各自只把日志转发到自己的前端，互不串流。
_active_writer: ContextVar = ContextVar("stream_logger_writer", default=None)
_sink_id = None
_installed = False
_lock = threading.Lock()


def _sink(message) -> None:
    """loguru sink：把日志内容转发给当前协程的活动 writer（作为 thought 块）。"""
    text = str(message)
    if not text or not text.strip():
        return
    writer = _active_writer.get()
    if writer is None:
        return
    try:
        # writer 是 LangGraph 的 stream_writer，直接 push thought 事件。
        # 与 orchestrator 的 _emit 同格式，前端会并入思考过程块。
        writer({"type": "thought", "content": text})
    except Exception:  # noqa: BLE001（转发失败不影响业务）
        pass


def _ensure_installed() -> None:
    """把 sink 挂到 loguru 根 logger（幂等，只挂一次）。"""
    global _sink_id, _installed
    if _installed:
        return
    _sink_id = logger.add(_sink, level="INFO", format="{message}")
    _installed = True


def start(writer) -> None:
    """对话节点开始：记录当前协程的活动 writer，开始转发日志。"""
    _ensure_installed()
    _active_writer.set(writer)


def stop() -> None:
    """对话节点结束：清除当前协程的活动 writer，停止转发。"""
    _active_writer.set(None)


@contextmanager
def bridge(writer):
    """上下文管理器：在 with 块内把 loguru INFO 日志转发给 writer。

    用法：
        with stream_logger.bridge(writer):
            ...
    """
    start(writer)
    try:
        yield
    finally:
        stop()
