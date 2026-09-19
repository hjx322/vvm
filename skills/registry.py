"""Skill 注册表：导入所有技能类 → 实例化 → 登记到一个字典 SKILL_REGISTRY"""

from typing import Dict

from skills.skills_optimize_srh.base import SkillHandler
from .mysql_query_skill import MySQLQuerySkill
from .milvus_query_skill import MilvusQuerySkill
from .web_search_skill import WebSearchSkill
from .derma_image_skill import ImageDetectHandler

# 为了向后兼容性，导出常用的类和函数
from skills.skills_optimize_srh.base import SkillResult, NecessaryDataResult
from .schemas import MySQLQuerySchema, MilvusQuerySchema, WebSearchSchema, MilvusSkillItem
from .milvus_query_skill import format_milvus_content

__all__ = [
    "SkillHandler",
    "MySQLQuerySkill",
    "MilvusQuerySkill",
    "WebSearchSkill",
    "MySQLQuerySchema",
    "MilvusQuerySchema",
    "WebSearchSchema",
    "MilvusSkillItem",
    "SkillResult",
    "NecessaryDataResult",
    "format_milvus_content",
    "SKILL_REGISTRY",
    "ImageDetectHandler"
]

SKILL_REGISTRY: Dict[str, SkillHandler] = {
    "mysql_query": MySQLQuerySkill(),
    "milvus_query": MilvusQuerySkill(),
    "web_search": WebSearchSkill(),
    "derma_image": ImageDetectHandler()
}
