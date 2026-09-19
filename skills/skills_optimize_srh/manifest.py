"""技能执行清单（Skill Manifest）—— 替代硬编码 SKILL_REGISTRY

借鉴 CoPaw 的 execution protocol：每个 skill 声明 entrypoint / runner / input_schema，
执行器根据声明直接调用脚本，无需 LLM 工具循环。
"""

import os
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


RunnerType = Literal["python_handler", "subprocess_script", "llm_tool_loop"]


@dataclass
class SkillManifest:
    """技能元数据与执行协议"""

    name: str                                      # 技能名称
    description: str = ""                          # 技能描述
    version: str = "1.0"                           # 版本号
    runner: RunnerType = "subprocess_script"       # 执行方式
    entrypoint: str = ""                           # 入口脚本 / handler name
    input_schema: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0                          # 超时（秒）
    keywords: List[str] = field(default_factory=list)  # 触发关键词（用于 Tier 1 匹配）
    triggers: List[str] = field(default_factory=list)  # 触发描述
    base_dir: str = ""                             # 技能根目录（执行时动态设置）

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "SkillManifest":
        """从 execution.yaml 文件加载清单"""
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=str(data.get("version", "1.0")),
            runner=data.get("runner", "subprocess_script"),
            entrypoint=data.get("entrypoint", ""),
            input_schema=data.get("input_schema", {}),
            timeout=float(data.get("timeout", 60)),
            keywords=data.get("keywords", []),
            triggers=data.get("triggers", []),
            base_dir=os.path.dirname(os.path.abspath(yaml_path)),
        )

    @classmethod
    def from_skill_md(cls, md_path: str) -> Optional["SkillManifest"]:
        """从 SKILL.md 的 YAML frontmatter 提取清单（兼容旧格式）

        如果没有 execution.yaml，就从 SKILL.md frontmatter 中提取基本信息，
        默认使用 subprocess_script runner。
        """
        if not os.path.exists(md_path):
            return None

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析 YAML frontmatter
        if not content.startswith("---"):
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return None

        name = fm.get("name", "")
        if not name:
            return None

        base_dir = os.path.dirname(os.path.abspath(md_path))

        return cls(
            name=name,
            description=fm.get("description", ""),
            version=str(fm.get("version", "1.0")),
            runner="subprocess_script",
            entrypoint="",
            keywords=fm.get("keywords", []),
            triggers=fm.get("triggers", []),
            base_dir=base_dir,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "runner": self.runner,
            "entrypoint": self.entrypoint,
            "timeout": self.timeout,
            "keywords": self.keywords,
            "triggers": self.triggers,
        }
