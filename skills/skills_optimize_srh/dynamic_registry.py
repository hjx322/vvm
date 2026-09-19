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

    Fix（段边界）：回调返回值必须**前后补空格**再交给 sub 拼接。原实现直接返回
    "我有 有湿 湿疹"，sub 会把它与下一段"该涂 涂什 …"首尾相接（原文的标点被替换掉
    了，不保留），于是相邻两段黏成一个跨段假 gram：
        '我有湿疹，该涂什么药'
          旧 -> ['我有', '有湿', '湿疹，该涂', '涂什', '什么', '么药']
                                              ↑ 真词"湿疹"消失，且"什么"成了噪声源
          新 -> ['我有', '有湿', '湿疹', '，', '该涂', '涂什', '什么', '么药']
    索引侧（空格分隔的关键词）切得出 '湿疹'，查询侧却切不出 —— 两边同词不同形，
    导致"湿疹"这类真词永远召回不到。**只在含标点的句子上暴露**（无标点时段落
    本就是完整连续段，不受影响），所以此前一直没被发现。
    """
    if not text:
        return ""

    def _ngram_seg(m: "re.Match") -> str:
        s = re.sub(r"\s+", "", m.group(0))
        if len(s) < n:
            return f" {s} "  # 同样补空格，防单字段与邻段粘连
        return " " + " ".join(s[i:i + n] for i in range(len(s) - n + 1)) + " "

    # 补空格会让段落处产生连续空格，统一折叠并去首尾
    return re.sub(r"\s+", " ", _CJK_RUN_RE.sub(_ngram_seg, text)).strip()


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
        # Tier 2 FTS5：按租户隔离的内存索引（key=user_id → 该租户专用的 :memory: 索引）
        # Fix: 原实现用单个 self._fts_conn + self._fts_user 保存"当前租户"的索引，
        # 而 registry 实例被所有会话共享 → 两个租户交替请求会反复重建索引（每次都
        # DELETE + 全量重插），且并发下 A 用户可能命中 B 用户的索引。改为按租户分连接。
        self._fts_conns: "OrderedDict[str, sqlite3.Connection]" = OrderedDict()
        self._FTS_MAX_TENANTS = 8  # 常驻索引的租户数上限（单租户索引很小，8 个足够）

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
        self._invalidate_fts(user_id)  # 只失效该租户索引，下次匹配重建

    def unregister_custom(self, user_id: str, name: str):
        """注销自定义技能"""
        key = f"{user_id}:{name}"
        self._manifests.pop(key, None)
        _USER_KEYWORD_MAP.pop(key, None)
        self._doc_cache.pop(key, None)
        logger.info(f"[DynamicSkillRegistry] 注销自定义技能: {key}")
        self._invalidate_fts(user_id)  # 只失效该租户索引，下次匹配重建

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
        self._invalidate_fts(user_id)  # 只失效该租户索引，下次匹配重建

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

    def match(
        self,
        query: str,
        user_id: str,
        pool: Optional[List[str]] = None,
    ) -> List[str]:
        """三层技能匹配

        Tier 1: 关键词规则匹配（0ms）
        Tier 2: SQLite FTS5 全文检索（~1-10ms，中文 2-gram）
        Tier 3: 返回空列表，由调用方降级到 LLM 选择

        Args:
            query: 用户输入文本
            user_id: 当前用户 ID
            pool: 候选技能作用域。None=全库（旧语义，行为不变）；
                  []=空作用域（恒返回 []，**不会**退化成全库）。
                  过滤在「匹配时」完成：池外技能的 Tier1 命中不进 hits，
                  因而不会误触发 `if not hits` 短路、把 Tier2 一起跳过。

        Returns:
            匹配到的技能名称列表；空列表表示需要 LLM 降级
        """
        # 必须用 `is not None` 判定：空列表是"空作用域"而非"全库"。
        # 写成真值判断会让空池静默退化为全库（作用域越权）。
        pool_set: Optional[set] = None if pool is None else {s for s in pool if s}

        hits: OrderedDict[str, None] = OrderedDict()  # 去重保序

        # Tier 1: 关键词匹配（内置 + 自定义）
        query_lower = query.lower()
        all_keyword_maps = [
            ("", _BUILTIN_KEYWORD_MAP),            # 内置技能（无前缀）
            ("", _USER_KEYWORD_MAP),                # 自定义技能（key 已有前缀）
        ]

        for _, kw_map in all_keyword_maps:
            for skill_key, keywords in kw_map.items():
                # 先解析「对外技能名」+ 作用域判定，再进关键词循环：
                # 池外技能既不进 hits，也不影响下方 Tier2 的短路判定
                if ":" in skill_key:
                    if not skill_key.startswith(f"{user_id}:"):
                        continue  # 其他用户的技能，整体跳过
                    name = skill_key[len(user_id) + 1:]
                else:
                    name = skill_key
                if pool_set is not None and name not in pool_set:
                    continue  # 池外：不记录、不短路
                for kw in keywords:
                    if kw.lower() in query_lower:
                        hits[name] = None
                        break

        # Tier 2: FTS5 全文检索 —— 与 Tier1 结果**取并集**，不再相互短路。
        #
        # Fix: 原实现是 `if not hits:` —— Tier1 只要有任一命中就跳过 Tier2。省下
        #      1-10ms，代价却是功能性漏召，因为 Tier1 只回答"有没有技能被触发"，
        #      回答不了"是不是全部"。实测（见 scripts/probe_tier_union.py）：
        #        "我有湿疹，该涂什么药"  Tier1 只中 derma_image（因"湿疹"），
        #          于是 mysql_query/milvus_query 再没机会被召回 —— 而"该涂什么药"
        #          真正需要的恰恰是这两者。
        #      根因是 Tier1 的 OR 语义 vs 用户问题的多意图（AND）语义不匹配：
        #      关键词表每技能仅十几词，一个常见词就能把整个 Tier2 拦掉。
        #      时间上这笔交易不划算：Tier1 0.068ms、Tier2 1-10ms、Tier3 1800ms，
        #      补跑 Tier2 相对 Tier3 仍是零头。
        # 注意：不设分数阈值。实测 FTS 分数被长句稀释（6-8 个 gram 时"命中 1 个
        #      真词"仅 0.167），而中文 2 字词命中 1 个 gram 恰恰是真信号；
        #      任何 >=0.17 的阈值都会砍掉真召回（"湿疹"=0.167）。
        #      误召由池作用域（池仅 2-4 个技能）+ 前置条件剪枝共同兜住。
        for name in self._fts_search(query, user_id, pool=pool_set):
            hits[name] = None

        result = list(hits.keys())
        if result:
            logger.info(
                f"[SkillMatch] Tier 1/2 命中: {result} (query='{query[:50]}...')"
                + (f" pool={sorted(pool_set)}" if pool_set is not None else "")
            )
        return result

    # ================================================================
    # Tier 2: SQLite FTS5 全文检索
    # ================================================================

    def _get_fts_conn(self, user_id: str) -> sqlite3.Connection:
        """取该租户的 FTS5 索引连接（首次访问惰性构建，按 LRU 上限淘汰）

        Fix: 索引按 user_id 分连接持有，避免多租户共享同一索引导致的
        "交替请求反复重建" 与 "并发下命中他人索引"。淘汰时显式 close，防内存泄漏。

        Args:
            user_id: 租户 ID（空串表示无租户上下文）

        Returns:
            该租户专用的内存 SQLite 连接
        """
        user_id = user_id or ""
        conn = self._fts_conns.get(user_id)
        if conn is not None:
            self._fts_conns.move_to_end(user_id)  # 标记为最近使用
            return conn

        conn = self._build_fts_index(user_id)
        self._fts_conns[user_id] = conn
        # LRU 淘汰：超出上限关闭最久未用的连接
        while len(self._fts_conns) > self._FTS_MAX_TENANTS:
            _, old = self._fts_conns.popitem(last=False)
            try:
                old.close()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[SkillMatch] 关闭淘汰的 FTS 索引失败: {e}")
        return conn

    def _invalidate_fts(self, user_id: str) -> None:
        """使某租户的 FTS5 索引失效（技能增删后调用，下次匹配时重建）

        Fix: 原实现只在 register_custom 里置 _fts_user=None，但 _fts_search 仅在
        "租户切换"时重建 → 同一租户内新增技能后，索引不会刷新，新技能在 Tier2
        永远搜不到。现按租户定向失效，技能变更即刻生效。
        """
        user_id = user_id or ""
        conn = self._fts_conns.pop(user_id, None)
        if conn is not None:
            try:
                conn.close()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[SkillMatch] 关闭失效的 FTS 索引失败: {e}")

    def _build_fts_index(self, user_id: str) -> sqlite3.Connection:
        """为「内置技能 + 指定用户自定义技能」新建 FTS5 索引并返回连接

        每个技能一条文档 = name + description + keywords + triggers + SKILL.md 正文，
        中文内容先经 2-gram 预处理，再以 unicode61 分词器索引。
        每次构建都新建独立的 :memory: 库（按租户隔离），不做原地 DELETE 复用。

        Args:
            user_id: 租户 ID（空串表示无租户上下文，只索引内置技能）

        Returns:
            建好索引的内存 SQLite 连接
        """
        conn = sqlite3.connect(":memory:")
        conn.execute(
            'CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5('
            'skill_key UNINDEXED, text, tokenize="unicode61")'
        )
        cur = conn.cursor()

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
        conn.commit()
        logger.info(
            f"[SkillMatch] 重建 FTS5 索引: 内置 {len(self._handlers)} + "
            f"用户 {user_id or '(无租户)'} {len(rows) - len(self._handlers)} 条"
        )
        return conn

    def _fts_search(
        self,
        query: str,
        user_id: str,
        pool: Optional[set] = None,
    ) -> List[str]:
        """FTS5 全文检索（Tier 2）

        用户输入拆 2-gram 逐一命中计数，按「命中比例降序」召回 top-N。
        中文 2 字词即可命中，长句 / 变体表述也能按比例召回，避免误杀。

        Args:
            pool: 已归一化的候选名集合（None=全库）。过滤发生在「计分阶段」，
                即在 ranked[:_FTS_MAX_RESULTS] 截断之前——若放到截断之后，
                top-N 被池外技能占满时池内技能一个都召不回。
        """
        # 防御：state 初始 user_id 可能为 None（digital_smart_doctor_agent.py:237 先置 None，
        # 由 pre_process 提取后才有值）。统一归为空串，避免 None 参与索引缓存键比较。
        user_id = user_id or ""

        grams = _ngram_tokenize(query).split()
        if not grams:
            return []

        # Fix: 取该租户专属索引（首次访问惰性构建），不再依赖共享的"当前租户"状态
        cur = self._get_fts_conn(user_id).cursor()
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
                if pool is not None and key not in pool:
                    continue  # 作用域外：不计分（= 截断前过滤）
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
