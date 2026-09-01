"""动态技能注册表 —— 替代硬编码 SKILL_REGISTRY

借鉴 Hermes Agent 的三层检索（规则匹配 → FTS5 → LLM 降级）
 + CoPaw 的渐进式披露（元数据先行，完整文档按需加载）
 + QClaw 的 Agent 权限隔离

改进：
  1. 支持运行时注册/注销（热加载）
  2. 3-tier 匹配：关键词规则 (0ms) → FTS5 语义检索 (1-5ms) → LLM 降级 (2-5s)
  3. 租户命名空间隔离：不同 user 的技能互不可见
  4. 统一执行：内置 handler + 自定义 script 走相同调度路径
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger
from langgraph.config import get_stream_writer

if TYPE_CHECKING:
    from skills.skills_optimize_srh.base import SkillHandler, SkillResult
    from skills.skills_optimize_srh.manifest import SkillManifest, RunnerType


def _find_bash() -> str | None:
    """在 Windows 上查找可用的 bash 解释器

    WSL 的 bash.exe 在某些环境下不可用（WSL 发行版未安装 bash），
    优先使用 Git Bash（通常更稳定）。
    """
    if sys.platform != "win32":
        return "bash"

    # 候选 bash 路径（按优先级排序）
    candidates = [
        # Git for Windows（最可靠）
        r"E:\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        # MSYS2
        r"C:\msys64\usr\bin\bash.exe",
        # Cygwin
        r"C:\cygwin64\bin\bash.exe",
        # WSL（通过 wsl.exe 直接调用，更可靠）
    ]

    for path in candidates:
        if os.path.exists(path):
            logger.debug(f"[BashFinder] 找到 bash: {path}")
            return path

    # 通过 where/which 查找
    found = shutil.which("bash")
    if found:
        # 排除 WindowsApps 别名（通常是 WSL 占位符，可能不可用）
        if "WindowsApps" not in found:
            logger.debug(f"[BashFinder] 通过 PATH 找到 bash: {found}")
            return found

    # 最后尝试 wsl.exe 直接调用
    wsl = shutil.which("wsl")
    if wsl and "WindowsApps" not in wsl:
        logger.debug(f"[BashFinder] 降级到 wsl bash: {wsl}")
        return f'{wsl} bash'

    return None


# ===== 内置技能的关键词映射（Tier 1 规则层）=====
_BUILTIN_KEYWORD_MAP: Dict[str, List[str]] = {
    "mysql_query": [
        "就诊记录", "病历", "检查结果", "诊断记录", "患者数据",
        "化验报告", "医嘱", "处方", "用药记录", "就诊历史",
        "门诊记录", "住院记录", "体检报告", "随访记录",
        "visit_record", "examine", "diagnosis", "prescription",
    ],
    "milvus_query": [
        "疾病定义", "药物说明", "医学知识", "治疗方案", "病症",
        "药品", "药理", "副作用", "禁忌", "适应症", "剂量",
        "什么是", "怎么治疗", "有哪些症状", "如何诊断",
        "流行病学", "病理", "临床表现", "鉴别诊断",
    ],
    "web_search": [
        "最新", "新闻", "近日", "最近", "当前", "实时",
        "今年", "最新研究", "最新指南", "最近发布",
        "搜索", "网上查", "查一下", "帮我查",
    ],
    "derma_image": [
        "皮肤病", "皮肤检测", "图片检测", "看图", "照片",
        "皮肤图片", "皮肤病变", "皮疹", "皮损", "痣",
        "皮肤癌", "黑色素瘤", "银屑病", "湿疹",
    ],
}

# 用户自定义技能关键词（由 upload_skill 时动态注册）
# key: "tenant_id:skill_name" → keywords
_USER_KEYWORD_MAP: Dict[str, List[str]] = {}

# ===== Tier 2: SQLite FTS5 全文检索（中文 2-gram 预处理）=====
# FTS5 trigram 分词器只索引 3-gram，对 1-2 字中文查询（如"病历""天气"）无法命中，
# 因此将中文连续段提前拆成 2-gram 空格串，用 unicode61 逐一索引——2 字词即精确命中。
_CJK_RUN_RE = re.compile(r"[一-鿿㐀-䶿]+")
_FTS_MAX_RESULTS = 3  # FTS5 召回上限

# ===== 技能执行错误判定（重试辅助）=====
# 参数/文件级错误：重试也无法改变"路径不存在/缺权重/参数非法"的事实，
# 命中即放弃重试，避免无效 LLM 调用与逐次累加的超长错误日志刷屏（Fix: 重试机制）
_PERMANENT_ERR_MARKS = (
    "文件不存在",
    "缺少 img_path",
    "权重缺失",
    "模型文件无效",
    "参数解析",
)


def _is_permanent_error(content) -> bool:
    """判断技能失败是否属于永久性（参数/文件级）错误"""
    return any(m in (content or "") for m in _PERMANENT_ERR_MARKS)


def _ngram_tokenize(text: str, n: int = 2) -> str:
    """中英混合文本 → n-gram 空格串（FTS5 中文二次检索用）

    - 中文连续段：按 2-gram 拆成相邻字符串（空格分隔）
    - 英文 / 数字 token：原样保留
    返回空格分隔的 token 串，供 unicode61 分词器逐个索引。

    注意用 re.sub 而非 re.split：split 按正则匹配点切分，会把整段
    中文当作"分隔符"吞掉而进不了循环；sub 则原地替换中文段，干净可靠。
    """
    if not text:
        return ""

    def _ngram_seg(m: "re.Match") -> str:
        s = re.sub(r"\s+", "", m.group(0))
        if len(s) < n:
            return s
        return " ".join(s[i:i + n] for i in range(len(s) - n + 1))

    return _CJK_RUN_RE.sub(_ngram_seg, text)


class DynamicSkillRegistry:
    """动态技能注册表

    用法：
        registry = DynamicSkillRegistry(llm)

        # 注册内置技能
        registry.register_builtin("mysql_query", MySQLQuerySkill(), manifest)

        # 注册自定义技能（按租户隔离）
        registry.register_custom(user_id, manifest)

        # 查询匹配
        skill_names = registry.match(query, user_id)

        # 执行
        result = await registry.execute(name, state, human_input, user_id)
    """

    def __init__(self, llm=None):
        self.llm = llm
        # 内置 Python handler: name → handler
        self._handlers: Dict[str, SkillHandler] = {}
        # 自定义技能清单: "tenant_id:name" → SkillManifest
        self._manifests: Dict[str, SkillManifest] = OrderedDict()
        # 技能文档缓存: "tenant_id:name" → SKILL.md 内容
        self._doc_cache: Dict[str, str] = {}
        # Tier 2 FTS5：懒初始化的内存索引（内存表仅存当前 user 的技能文档）
        self._fts_conn: Optional[sqlite3.Connection] = None
        self._fts_user: Optional[str] = None  # 当前 FTS 索引对应的 user；None=未构建/已失效

    # ================================================================
    # 注册 / 注销
    # ================================================================

    def register_builtin(
        self,
        name: str,
        handler: SkillHandler,
        manifest: Optional[SkillManifest] = None,
    ):
        """注册内置技能（Python handler 直接调用）"""
        self._handlers[name] = handler
        logger.info(f"[DynamicSkillRegistry] 注册内置技能: {name}")

    def register_custom(self, user_id: str, manifest: SkillManifest):
        """注册自定义技能（按租户隔离）

        Args:
            user_id: 技能所属用户 ID
            manifest: 技能执行清单
        """
        key = f"{user_id}:{manifest.name}"
        self._manifests[key] = manifest
        # 注册关键词
        if manifest.keywords:
            _USER_KEYWORD_MAP[key] = manifest.keywords
        logger.info(
            f"[DynamicSkillRegistry] 注册自定义技能: {key} "
            f"(runner={manifest.runner}, keywords={manifest.keywords})"
        )
        self._fts_user = None  # FTS 索引失效，下次匹配重建

    def unregister_custom(self, user_id: str, name: str):
        """注销自定义技能"""
        key = f"{user_id}:{name}"
        self._manifests.pop(key, None)
        _USER_KEYWORD_MAP.pop(key, None)
        self._doc_cache.pop(key, None)
        logger.info(f"[DynamicSkillRegistry] 注销自定义技能: {key}")
        self._fts_user = None  # FTS 索引失效，下次匹配重建

    def unregister_all_user(self, user_id: str):
        """注销某用户的所有自定义技能"""
        prefix = f"{user_id}:"
        keys = [k for k in self._manifests if k.startswith(prefix)]
        for k in keys:
            self._manifests.pop(k, None)
            _USER_KEYWORD_MAP.pop(k, None)
            self._doc_cache.pop(k, None)
        logger.info(
            f"[DynamicSkillRegistry] 注销用户 {user_id} 的 {len(keys)} 个技能"
        )
        self._fts_user = None  # FTS 索引失效，下次匹配重建

    # ================================================================
    # 查询
    # ================================================================

    def get_builtin_names(self) -> List[str]:
        return list(self._handlers.keys())

    def get_custom_names(self, user_id: str) -> List[str]:
        """获取某用户的所有自定义技能名称"""
        prefix = f"{user_id}:"
        return [
            k[len(prefix):] for k in self._manifests if k.startswith(prefix)
        ]

    def get_all_available(self, user_id: str) -> List[Dict[str, Any]]:
        """获取某用户可用的所有技能（内置 + 自定义）

        Returns:
            [{"name": ..., "description": ..., "runner": ..., "keywords": [...]}, ...]
        """
        skills = []

        # 内置技能
        for name, handler in self._handlers.items():
            manifest = None
            for m in self._manifests.values():
                if m.name == name:
                    manifest = m
                    break
            skills.append({
                "name": name,
                "description": manifest.description if manifest else "",
                "runner": "python_handler",
                "keywords": _BUILTIN_KEYWORD_MAP.get(name, []),
            })

        # 用户自定义技能
        prefix = f"{user_id}:"
        for key, manifest in self._manifests.items():
            if key.startswith(prefix):
                skills.append(manifest.to_dict())

        return skills

    def is_registered(self, name: str, user_id: str = "") -> bool:
        """检查技能是否已注册"""
        if name in self._handlers:
            return True
        if user_id and f"{user_id}:{name}" in self._manifests:
            return True
        if f"system:{name}" in self._manifests:
            return True
        return False

    # ================================================================
    # 3-Tier 技能匹配
    # ================================================================

    def match(self, query: str, user_id: str) -> List[str]:
        """三层技能匹配

        Tier 1: 关键词规则匹配（0ms）
        Tier 2: SQLite FTS5 全文检索（~1-10ms，中文 2-gram）
        Tier 3: 返回空列表，由调用方降级到 LLM 选择

        Args:
            query: 用户输入文本
            user_id: 当前用户 ID

        Returns:
            匹配到的技能名称列表；空列表表示需要 LLM 降级
        """
        hits: OrderedDict[str, None] = OrderedDict()  # 去重保序

        # Tier 1: 关键词匹配（内置 + 自定义）
        query_lower = query.lower()
        all_keyword_maps = [
            ("", _BUILTIN_KEYWORD_MAP),            # 内置技能（无前缀）
            ("", _USER_KEYWORD_MAP),                # 自定义技能（key 已有前缀）
        ]

        for _, kw_map in all_keyword_maps:
            for skill_key, keywords in kw_map.items():
                for kw in keywords:
                    if kw.lower() in query_lower:
                        # 提取技能名：内置直接用，自定义需要去前缀
                        if ":" in skill_key:
                            if skill_key.startswith(f"{user_id}:"):
                                hits[skill_key[len(user_id) + 1:]] = None
                            # else: 其他用户的技能，跳过
                        else:
                            hits[skill_key] = None
                        break

        # Tier 2: FTS5 全文检索 —— 覆盖关键词漏网的长句 / 变体表述
        if not hits:
            for name in self._fts_search(query, user_id):
                hits[name] = None

        result = list(hits.keys())
        if result:
            logger.info(
                f"[SkillMatch] Tier 1/2 命中: {result} (query='{query[:50]}...')"
            )
        return result

    # ================================================================
    # Tier 2: SQLite FTS5 全文检索
    # ================================================================

    def _build_fts_index(self, user_id: str) -> None:
        """为「内置技能 + 指定用户自定义技能」重建 FTS5 索引

        每个技能一条文档 = name + description + keywords + triggers + SKILL.md 正文，
        中文内容先经 2-gram 预处理，再以 unicode61 分词器索引。
        """
        if self._fts_conn is None:
            self._fts_conn = sqlite3.connect(":memory:")
            self._fts_conn.execute(
                'CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5('
                'skill_key UNINDEXED, text, tokenize="unicode61")'
            )

        cur = self._fts_conn.cursor()
        cur.execute("DELETE FROM skill_fts")  # 重建，保证与注册表一致

        rows: List[tuple] = []
        # 内置技能：name + docstring + 关键词 + SKILL.md 正文
        for name, handler in self._handlers.items():
            desc = (handler.__class__.__doc__ or "").strip()
            text = " ".join([
                name, desc,
                " ".join(_BUILTIN_KEYWORD_MAP.get(name, [])),
                self._read_skill_md(name, user_id) or "",
            ])
            rows.append((name, _ngram_tokenize(text)))

        # 用户自定义技能（仅该 user）
        prefix = f"{user_id}:"
        for key, manifest in self._manifests.items():
            if not key.startswith(prefix):
                continue
            text = " ".join([
                manifest.name,
                manifest.description,
                " ".join(manifest.keywords),
                " ".join(manifest.triggers),
                self._read_skill_md(manifest.name, user_id) or "",
            ])
            rows.append((manifest.name, _ngram_tokenize(text)))

        if rows:
            cur.executemany("INSERT INTO skill_fts VALUES (?,?)", rows)
        self._fts_conn.commit()
        logger.info(
            f"[SkillMatch] 重建 FTS5 索引: 内置 {len(self._handlers)} + "
            f"用户 {user_id} {len(rows) - len(self._handlers)} 条"
        )

    def _fts_search(self, query: str, user_id: str) -> List[str]:
        """FTS5 全文检索（Tier 2）

        用户输入拆 2-gram 逐一命中计数，按「命中比例降序」召回 top-N。
        中文 2 字词即可命中，长句 / 变体表述也能按比例召回，避免误杀。
        """
        # 防御：state 初始 user_id 可能为 None（digital_smart_doctor_agent.py:237 先置 None，
        # 由 pre_process 提取后才有值）。None 与 _fts_user 初始值 None 相等会误判"已构建"
        # 而跳过 _build_fts_index，导致 _fts_conn 还是 None。故先归为空串再比较。
        user_id = user_id or ""
        if self._fts_conn is None or self._fts_user != user_id:
            self._build_fts_index(user_id)
            self._fts_user = user_id

        grams = _ngram_tokenize(query).split()
        if not grams:
            return []

        cur = self._fts_conn.cursor()
        scores: Dict[str, int] = {}
        for g in grams:
            g = g.replace('"', "")
            if not g:
                continue
            try:
                rows = cur.execute(
                    "SELECT skill_key FROM skill_fts WHERE skill_fts MATCH ?",
                    (f'"{g}"',),
                ).fetchall()
            except sqlite3.OperationalError as e:
                logger.debug(f"[SkillMatch] FTS 查询异常 gram={g!r}: {e}")
                continue
            for (key,) in rows:
                scores[key] = scores.get(key, 0) + 1

        ranked = sorted(
            scores.items(), key=lambda kv: -kv[1] / len(grams)
        )
        result = [k for k, _ in ranked[:_FTS_MAX_RESULTS]]
        if result:
            logger.info(
                f"[SkillMatch] Tier 2 FTS5 命中: {result} "
                f"(query='{query[:50]}...')"
            )
        return result

    # ================================================================
    # 技能文档
    # ================================================================

    def get_skill_doc(self, name: str, user_id: str) -> Optional[str]:
        """获取技能文档内容（带缓存）"""
        # 内置技能：从 .claude/skills/ 读取
        if name in self._handlers:
            cache_key = f"builtin:{name}"
            if cache_key in self._doc_cache:
                return self._doc_cache[cache_key]
            doc = self._read_skill_md(name, user_id)
            if doc:
                self._doc_cache[cache_key] = doc
            return doc

        # 自定义技能
        cache_key = f"{user_id}:{name}"
        if cache_key in self._doc_cache:
            return self._doc_cache[cache_key]
        doc = self._read_skill_md(name, user_id)
        if doc:
            self._doc_cache[cache_key] = doc
        return doc

    def _read_skill_md(self, name: str, user_id: str) -> Optional[str]:
        """从文件系统读取 SKILL.md"""
        search_paths = []

        # 用户自定义技能
        if user_id:
            search_paths.append(
                os.path.join("user_skills", user_id, name, "current", "SKILL.md")
            )
            # 向下兼容：旧的 medical_record_no 路径
            search_paths.append(
                os.path.join("user_skills", user_id, name, "SKILL.md")
            )

        # 全局内置技能
        search_paths.append(
            os.path.join(".claude", "skills", name, "SKILL.md")
        )

        for path in search_paths:
            abs_path = os.path.realpath(path)
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    return content
                except Exception as e:
                    logger.error(f"读取 SKILL.md 失败 {abs_path}: {e}")

        return None

    # ================================================================
    # 技能执行 —— 统一入口
    # ================================================================

    async def execute(
        self,
        name: str,
        state,
        human_input: str,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """统一技能执行入口

        根据注册类型自动路由：
        - 内置技能 → Python handler 直接调用
        - 自定义技能 (subprocess_script) → 直接执行脚本
        - 自定义技能 (llm_tool_loop) → LLM 工具循环（降级）

        Args:
            name: 技能名称
            state: 当前 Agent 状态
            human_input: 用户输入
            user_id: 租户 ID

        Returns:
            {"skill_name": str, "success": bool, "content": str}
        """
        try:
            # 内置技能：Python handler
            if name in self._handlers:
                return await self._execute_handler(
                    name, state, human_input
                )

            # 自定义技能：根据 manifest 执行
            key = f"{user_id}:{name}"
            if key in self._manifests:
                manifest = self._manifests[key]
                if manifest.runner == "subprocess_script":
                    return await self._execute_script(
                        manifest, human_input, user_id
                    )
                elif manifest.runner == "python_handler":
                    return await self._execute_python_handler(
                        manifest, human_input, user_id
                    )
                elif manifest.runner == "llm_tool_loop":
                    return await self._execute_llm_loop_async(
                        manifest, human_input, state
                    )
                else:
                    return {
                        "skill_name": name,
                        "success": False,
                        "content": f"不支持的 runner 类型: {manifest.runner}",
                    }

            return {
                "skill_name": name,
                "success": False,
                "content": f"技能未注册: {name}",
            }

        except Exception as e:
            logger.exception(f"执行技能异常: {name}")
            return {
                "skill_name": name,
                "success": False,
                "content": f"执行异常: {e}",
            }

    # ================================================================
    # 执行器：内置 Python handler
    # ================================================================

    async def _execute_handler(
        self, name: str, state, human_input: str
    ) -> Dict[str, Any]:
        """执行内置 Python handler"""
        handler = self._handlers[name]

        # 获取 custom 流 writer：执行失败/重试时把过程实时推给前端。
        # try 包裹：非 langgraph 上下文直接调用时静默降级为不发事件。
        try:
            stream_writer = get_stream_writer()
        except Exception:
            stream_writer = None

        def _emit_thought(text: str) -> None:
            if stream_writer is not None:
                stream_writer({"type": "thought", "content": text})

        # 准备必要数据
        necessary_data = await handler.prepare_necessary_data_async(state)
        if not necessary_data.success:
            if necessary_data.content == "MySQL 查询缺少病历号":
                return {
                    "skill_name": name,
                    "success": False,
                    "content": "",
                    "silent": True,
                }
            return {
                "skill_name": name,
                "success": False,
                "content": necessary_data.content,
            }

        # 读取技能文档
        user_id = state.get("user_id", "")
        skill_doc = self.get_skill_doc(name, user_id) or ""

        # LLM 生成参数并执行
        messages = [
            {"role": "system", "content": f"Skill content:\n{skill_doc}, Necessary data:\n{necessary_data.content}"},
            {"role": "user", "content": human_input},
        ]

        # 导入 langchain 消息类型
        from langchain_core.messages import SystemMessage, HumanMessage

        lc_messages = [
            SystemMessage(content=self._sanitize(messages[0]["content"])),
            HumanMessage(content=self._sanitize(messages[1]["content"])),
        ]

        search_response = await handler.execute_with_llm_async(self.llm, lc_messages)

        # 重试机制（最多 3 次）
        retry_count = 0
        while not search_response.success and retry_count < 3:
            # 参数/文件级永久错误：重试无法改变事实，直接放弃（Fix: 减少无效重试与超长错误刷屏）
            if _is_permanent_error(search_response.content):
                logger.warning(
                    f"Skill {name} 参数级错误，放弃重试: {str(search_response.content)[:200]}"
                )
                _emit_thought(
                    f"⚠️ {name}："
                    + (
                        " ".join(str(search_response.content).split())[:100]
                        or "参数级错误"
                    )
                    + "，放弃重试",
                )
                break
            logger.warning(
                f"Skill {name} 执行失败, 重试 ({retry_count + 1}/3): {search_response.content}"
            )
            _emit_thought(f"⚠️ {name} 执行失败，自动重试（{retry_count + 1}/3）")
            from prompt.query_result_prompt import PROMPT_QUERY_ERROR_RETRY

            lc_messages.append(
                SystemMessage(
                    content=PROMPT_QUERY_ERROR_RETRY.format(
                        error_content=search_response.content
                    )
                )
            )
            # 重试必须走同一 structured-output 链路（Fix: 原逻辑用 llm.ainvoke 无约束，
            # 会把 LLM 自然语言话术当参数传给 handler，导致 "文件不存在: <一段人话>" 式错误）
            search_response = await handler.execute_with_llm_async(self.llm, lc_messages)
            retry_count += 1

        return {
            "skill_name": name,
            "success": search_response.success,
            "content": search_response.content,
        }

    # ================================================================
    # 执行器：subprocess 脚本
    # ================================================================

    async def _execute_script(
        self, manifest: SkillManifest, human_input: str, user_id: str
    ) -> Dict[str, Any]:
        """直接执行脚本（替代原来的 LLM 工具循环）

        这是最大的性能改进——自定义技能不再需要 2-11 次 LLM 调用。
        """
        if not manifest.entrypoint:
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"技能 {manifest.name} 未指定 entrypoint",
            }

        base_dir = manifest.base_dir
        if not base_dir or not os.path.isdir(base_dir):
            # 尝试从标准路径查找
            base_dir = os.path.join("user_skills", user_id, manifest.name, "current")

        # 统一转换为绝对路径（Git Bash 需要绝对路径）
        base_dir = os.path.realpath(base_dir)
        if not os.path.isdir(base_dir):
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"技能目录不存在: {manifest.name}",
            }

        script_path = os.path.realpath(os.path.join(base_dir, manifest.entrypoint))
        if not os.path.exists(script_path):
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"入口脚本不存在: {manifest.entrypoint}",
            }

        # 构建命令
        ext = os.path.splitext(script_path)[1].lower()
        args = human_input  # 用户输入直接作为脚本参数

        if ext == ".sh":
            if sys.platform == "win32":
                bash = _find_bash()
                if not bash:
                    return {
                        "skill_name": manifest.name,
                        "success": False,
                        "content": (
                            "无法找到 bash 解释器。请安装 Git for Windows "
                            "(https://git-scm.com) 或 WSL。"
                        ),
                    }
                cmd = f'{bash} "{script_path}" {args}'.strip()
            else:
                cmd = f'sh "{script_path}" {args}'.strip()
        elif ext == ".py":
            python_exe = shutil.which("python") or "python"
            cmd = f'{python_exe} "{script_path}" {args}'.strip()
        else:
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"不支持的脚本类型: {ext}",
            }

        logger.info(f"[SkillExec] 直接执行脚本: {cmd[:100]}...")

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=manifest.timeout,
                    cwd=base_dir,
                ),
            )

            if result.returncode == 0:
                output = result.stdout.strip()
                content = output if output else "脚本执行成功，无输出。"
                return {
                    "skill_name": manifest.name,
                    "success": True,
                    "content": content,
                }
            else:
                error = result.stderr.strip()
                return {
                    "skill_name": manifest.name,
                    "success": False,
                    "content": f"脚本执行失败 (Exit {result.returncode}): {error}",
                }

        except subprocess.TimeoutExpired:
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"脚本执行超时（{manifest.timeout}秒）",
            }
        except Exception as e:
            return {
                "skill_name": manifest.name,
                "success": False,
                "content": f"执行异常: {e}",
            }

    # ================================================================
    # 执行器：自定义 Python handler / LLM 工具循环（降级保留）
    # ================================================================

    async def _execute_python_handler(
        self, manifest: SkillManifest, human_input: str, user_id: str
    ) -> Dict[str, Any]:
        """执行自定义 Python handler（尚未实现，预留接口）"""
        return {
            "skill_name": manifest.name,
            "success": False,
            "content": "python_handler 自定义执行器尚未实现",
        }

    async def _execute_llm_loop_async(
        self, manifest: SkillManifest, human_input: str, state
    ) -> Dict[str, Any]:
        """LLM 工具循环执行（应由 dispatcher 拦截处理，此方法为兜底）

        正常情况下 skill_dispatcher._execute_skill_core() 会在调用
        registry.execute() 之前拦截 llm_tool_loop 类型的技能，
        使用其自身的 _execute_external_skill() 方法处理。

        如果走到这里，说明调用方没有正确处理 llm_tool_loop 路由。
        """
        logger.error(
            f"llm_tool_loop 技能 {manifest.name} 未被 dispatcher 拦截，"
            f"注册表无法独立执行 LLM 循环（缺少 LLM 实例）。"
            f"请确保由 UnifiedSkillDispatcher.dispatch() 调用。"
        )
        return {
            "skill_name": manifest.name,
            "success": False,
            "content": (
                f"技能 {manifest.name} 需要 LLM 工具循环执行，"
                f"但当前调用路径不支持。请通过 API 对话接口调用此技能。"
            ),
        }

    # ================================================================
    # 辅助方法
    # ================================================================

    @staticmethod
    def _sanitize(text: str) -> str:
        """清理 UTF-8 surrogate 字符"""
        if not text:
            return text
        try:
            return text.encode("utf-8", errors="surrogatepass").decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return "".join(
                c for c in text if not (0xD800 <= ord(c) <= 0xDFFF)
            )
