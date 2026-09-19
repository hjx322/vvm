"""MySQL Query Skill 实现"""

import os
import sys

sys.path.insert(0, os.path.abspath(".claude/skills"))
from mysql_query.scripts.search import search_with_retry

from skills.skills_optimize_srh.base import SkillHandler, SkillResult, NecessaryDataResult
from .schemas import MySQLQuerySchema


class MySQLQuerySkill(SkillHandler):
    """从 MySQL 数据库检索患者个人医疗数据"""

    schema = MySQLQuerySchema

    async def execute_with_llm_async(self, llm, messages) -> SkillResult:
        """
        使用 LLM 生成结构化参数并执行 MySQL 查询。

        Args:
            llm: 语言模型实例，需支持 with_structured_output 方法
            messages: 当前的对话历史列表

        Returns:
            SkillResult: 技能执行结果对象，包含 success 状态和 content 内容
        """
        try:
            structured_llm = llm.with_structured_output(self.schema)
            params_obj = structured_llm.invoke(messages)
            if not params_obj:
                return SkillResult(False, "LLM 返回了空的结构化输出")
            return self.call(params_obj.model_dump_json())
        except Exception as e:
            return SkillResult(False, f"MySQL 参数生成失败: {str(e)}")

    def prepare_necessary_data(self, state) -> NecessaryDataResult:
        """检查必要数据是否存在"""
        if "medical_record_no" not in state:
            return NecessaryDataResult(False, "MySQL 查询缺少病历号")
        if "crm" not in state:
            return NecessaryDataResult(False, "MySQL 查询缺少CRM参数")

        return NecessaryDataResult(
            True,
            f"病历号/medical_record_no:{state['medical_record_no']}\
                                            crm:{state['crm']}",
        )

    def call(self, input_param: str) -> SkillResult:
        """执行 MySQL 查询（带重试机制）"""
        try:
            start = input_param.find("{")
            if start == -1:
                start = 0

            content = search_with_retry(str(input_param[start:]), max_retries=3)

            return SkillResult(
                success=True,
                content=content,
            )
        except Exception as e:
            return SkillResult(
                success=False,
                error=f"MySQL 查询失败: {str(e)}"
            )
