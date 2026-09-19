import os
import shutil
import zipfile
import json
import re
import tempfile
from pathlib import Path
from typing import Dict, Any


class FileManager:
    """Manages skill file versioning (current/previous)"""

    def __init__(self, base_path: str = "./user_skills"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_skill_dir(self, user_id: str, skill_id: str) -> Path:
        """Get skill directory for a user"""
        return self.base_path / user_id / skill_id

    def extract_zip_to_temp(self, zip_path: str, skill_id: str) -> Path:
        """Extract ZIP file to temporary directory"""
        temp_dir = self.base_path / ".temp" / skill_id
        temp_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        return temp_dir

    def save_skill_version(self, user_id: str, skill_id: str, source_dir: Path) -> str:
        """Save skill and return current path"""
        skill_dir = self.get_skill_dir(user_id, skill_id)
        skill_dir.mkdir(parents=True, exist_ok=True)

        current_path = skill_dir / "current"

        # Remove old version if exists
        if current_path.exists():
            shutil.rmtree(current_path)

        shutil.copytree(source_dir, current_path)
        # 返回相对路径，使用 / 分隔符和 ./ 前缀（跨平台兼容）
        return "./" + current_path.as_posix()

    def delete_skill_files(self, user_id: str, skill_id: str):
        """Delete all skill files for a user"""
        skill_dir = self.get_skill_dir(user_id, skill_id)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

    def cleanup_temp_files(self, skill_id: str):
        """Clean up temporary extraction directory"""
        temp_dir = self.base_path / ".temp" / skill_id
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    def parse_skill_metadata(self, zip_file_path: str) -> Dict[str, Any]:
        """
        从ZIP包中的SKILL.md文件解析元数据

        返回格式：
        {
            "skill_id": "weather-zh",（从frontmatter的name字段）
            "description": "技能简述",
            "language": "bash",
            "metadata": {...},
            "content": "整个SKILL.md文件内容"
        }

        异常处理：
        - SKILL.md 不存在 → ValueError
        - Frontmatter 格式错误 → ValueError
        - name 字段缺失 → ValueError
        """
        # 提取ZIP到临时目录
        temp_dir = Path(tempfile.mkdtemp())
        try:
            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)

            # 查找 SKILL.md 文件
            skill_md_path = None
            for file in temp_dir.rglob('SKILL.md'):
                skill_md_path = file
                break

            if not skill_md_path:
                raise ValueError(f"SKILL.md 文件不存在于 ZIP 包中：{zip_file_path}")

            # 读取完整内容
            with open(skill_md_path, 'r', encoding='utf-8') as f:
                full_content = f.read()

            # 提取 frontmatter（--- 之间的内容）
            frontmatter_match = re.match(r'^---\n(.*?)\n---', full_content, re.DOTALL)
            if not frontmatter_match:
                raise ValueError(f"SKILL.md 格式错误：找不到 frontmatter（需要 --- 开头和结尾）")

            frontmatter_text = frontmatter_match.group(1)

            # 解析 frontmatter 行
            metadata = {}
            skill_id = None
            description = None
            language = None

            for line in frontmatter_text.split('\n'):
                if not line.strip():
                    continue

                # 处理 key: value 格式
                if ':' in line:
                    key, _, value = line.partition(':')
                    key = key.strip()
                    value = value.strip()

                    if key == 'name':
                        skill_id = value
                    elif key == 'description':
                        description = value
                    elif key == 'language':
                        language = value
                    elif key == 'metadata':
                        # 尝试解析为 JSON
                        try:
                            metadata = json.loads(value)
                        except json.JSONDecodeError:
                            # 如果不是标准 JSON，保存为字符串
                            metadata = {"raw": value}
            file_name_with_ext = os.path.basename(zip_file_path)

            # 2. 分离文件名和后缀；技能 id 优先取 frontmatter 的 name（上传临时 zip 时文件名随机），无则用 zip 文件名
            file_name_only = os.path.splitext(file_name_with_ext)[0]

            return {
                "skill_id": skill_id or file_name_only,
                "description": description or "",
                "language": language or "python3",
                "metadata": metadata,
                "content": full_content,
            }
        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir, ignore_errors=True)
