"""Skill 基类定义及公共数据结构"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


# ===== 公共数据结构 =====

@dataclass
class SkillResult:
    success: bool
    content: str
    raw: Any = None


@dataclass
class NecessaryDataResult:
    success: bool
    content: str


class SkillHandler(ABC):
    """所有 Skill 的基类"""

    schema: Optional[Any] = None

    @abstractmethod
    def prepare_necessary_data(self, state) -> NecessaryDataResult:
        """准备必要数据"""
        ...

    @abstractmethod
    def call(self, input_param: str) -> SkillResult:
        """调用技能"""
        ...


    def execute_with_llm(self, llm, messages) -> SkillResult:
        """
        使用大型语言模型生成参数并调用工具。
        必须由子类实现。
        """
        ...

    # ===== 异步方法 =====

    async def prepare_necessary_data_async(self, state) -> NecessaryDataResult:
        """
        异步版本的prepare_necessary_data。
        默认实现：在executor中运行同步方法。
        子类可以override以提供真正的异步实现。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.prepare_necessary_data, state)

    async def call_async(self, input_param: str) -> SkillResult:
        """
        异步版本的call。
        默认实现：在executor中运行同步方法。
        子类可以override以提供真正的异步实现。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.call, input_param)

    async def execute_with_llm_async(self, llm, messages) -> SkillResult:
        """
        异步版本的execute_with_llm。
        使用LLM的异步API生成参数并调用工具。
        """
        try:
            structured_llm = llm.with_structured_output(self.schema)
            params_obj = await structured_llm.ainvoke(messages)
            if not params_obj:
                return SkillResult(False, "LLM 返回了空的结构化输出")
            return await self.call_async(params_obj.model_dump_json())
        except Exception as e:
            return SkillResult(False, f"参数生成失败: {str(e)}")
