# skill的具体逻辑在.claude中保存，这里写的是调用逻辑

# 核心导出
from skills.registry import (
    SKILL_REGISTRY,
    SkillHandler,
    MySQLQuerySkill,
    MilvusQuerySkill,
    WebSearchSkill,
    MySQLQuerySchema,
    MilvusQuerySchema,
    WebSearchSchema,
    MilvusSkillItem,
    SkillResult,
    NecessaryDataResult,
    format_milvus_content,
)

# 新架构：动态注册表 + 技能清单
from skills.skills_optimize_srh.dynamic_registry import DynamicSkillRegistry
from skills.skills_optimize_srh.manifest import SkillManifest

__all__ = [
    "SKILL_REGISTRY",
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
    "DynamicSkillRegistry",
    "SkillManifest",
]
