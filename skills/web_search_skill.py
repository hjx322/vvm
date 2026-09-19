"""Web Search Skill 实现"""

import os
import sys

sys.path.insert(0, os.path.abspath(".claude/skills"))
from web_search.scripts.search import search as web_search

from langchain_core.messages import SystemMessage

from skills.skills_optimize_srh.base import SkillHandler, SkillResult, NecessaryDataResult
from .schemas import WebSearchSchema


class WebSearchSkill(SkillHandler):
    """从互联网搜索最新信息和通用知识"""

    schema = WebSearchSchema

    async def execute_with_llm_async(self, llm, messages) -> SkillResult:
        """
        使用 LLM 生成结构化参数并执行网络搜索。

        Args:
            llm: 语言模型实例，需支持 with_structured_output 方法
            messages: 当前的对话历史列表

        Returns:
            SkillResult: 技能执行结果对象，包含 success 状态和 content 内容
        """
        try:
            structured_llm = llm.with_structured_output(self.schema)
            # Fix: 混合主题（实时信息+其它）时聚焦实时部分——避免"深圳天气+湿疹用药"
            # 被 LLM 合成一个 query，导致 Tavily 返回的全是医学文章、天气数据丢失。
            query_focus_hint = SystemMessage(
                content=(
                    "生成 query 时：若用户输入同时含实时信息请求（如天气、实时新闻、"
                    "股票行情、实时价格）与其它主题，query 只聚焦实时信息部分"
                    "（地点+实时词+简短，如\"深圳 今天 天气\"），确保搜索能返回实时数据；"
                    "医学知识类内容由知识库检索负责，不要拼进联网 query。"
                )
            )
            params_obj = structured_llm.invoke(messages + [query_focus_hint])
            if not params_obj:
                return SkillResult(False, "LLM 返回了空的结构化输出")
            return self.call(params_obj.model_dump_json())
        except Exception as e:
            return SkillResult(False, f"网络搜索参数生成失败: {str(e)}")

    def prepare_necessary_data(self, state) -> NecessaryDataResult:
        """网络搜索不需要必要数据"""
        return NecessaryDataResult(True, "")

    def call(self, input_param: str) -> SkillResult:
        """执行网络搜索"""
        start = input_param.find("{")
        if start == -1:
            start = 0
        content = web_search(str(input_param[start:]))
        return SkillResult(
            success=True,
            content=content,
        )
