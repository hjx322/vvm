"""
Unified Skill Dispatcher (v2) — 统一技能调度器

v2 改进（基于 Hermes/CoPaw/QClaw 框架借鉴）：
  1. 技能匹配：3-tier（关键词规则 → FTS5 → LLM 降级）
  2. 技能执行：DynamicSkillRegistry 统一路由，自定义技能直接执行脚本
  3. 租户隔离：基于 user_id 的存储/权限/执行沙箱
  4. 移除 OpenSkill 依赖：本地文件 + DB 替代 npx openskills

使用方式：
    from skill_dispatcher import UnifiedSkillDispatcher

    dispatcher = UnifiedSkillDispatcher(llm)
    result = await dispatcher.dispatch(state)
"""

import asyncio
import json
import os
import re
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from loguru import logger
from pydantic import BaseModel, Field

from agent.core.state import DigitalSmartDoctorState
from agent.tools.skill_tools import make_skill_tools
from prompt.external_skill_prompt import EXECUTE_SYSTEM_INSTRUCTION
from skills.skills_optimize_srh.dynamic_registry import DynamicSkillRegistry, _BUILTIN_KEYWORD_MAP
from skills.skills_optimize_srh.manifest import SkillManifest
from skills.registry import SKILL_REGISTRY


# ===== 结构化输出模型 =====

class SkillCallItem(BaseModel):
    """单个技能调用项"""
    skill_name: str = Field(description="技能名称，必须从可用技能清单中选择")
    reason: str = Field(description="选择该技能的原因（简短说明）")


class SkillSelectionResult(BaseModel):
    """LLM 技能选择的结构化输出"""
    needs_skill: bool = Field(description="是否需要调用技能来回答用户问题")
    skills: List[SkillCallItem] = Field(
        default_factory=list, description="需要调用的技能列表"
    )
    reply: Optional[str] = Field(
        default=None, description="如果不调用技能，直接回复用户的内容"
    )


# ===== 技能选择 Prompt =====

SKILL_SELECTION_SYSTEM_PROMPT = """你是一个**智能任务调度器**。
你的目标是分析用户输入，判断是否需要调用技能，并选择合适的技能。

### 可用技能清单
{skills_description}

### 调度规则

1. **意图扫描**：分析用户输入，判断需要哪些技能来获取信息
2. **多选支持**：如果用户的问题涉及多个领域，可以同时选择多个技能
3. **技能匹配规则**：
   - 涉及患者个人数据（就诊记录、检查结果、病历等）→ 选中 `mysql_query`
   - 涉及疾病定义、药物说明、医学知识 → 选中 `milvus_query`
   - 需要互联网搜索最新信息 → 选中 `web_search`
   - 涉及皮肤病图片检测 → 选中 `derma_image`
   - 涉及其他外部技能 → 选中对应技能名称
4. **无需技能**：如果用户的问题不需要查询外部数据（如闲聊、通用问答），设置 needs_skill=False 并在 reply 中直接回答

### 重要
- skill_name 必须严格从可用技能清单中选择，不要编造技能名称
- 如果不确定是否需要技能，宁可选择调用
- 不要输出分析过程，只输出结构化结果
"""


class UnifiedSkillDispatcher:
    """统一技能调度器 (v2)

    v2 核心改进：
    - 技能选择用 3-tier 匹配（关键词优先，LLM 降级）
    - 自定义技能直接执行脚本，不再走 LLM 工具循环
    - 租户隔离：基于 user_id 的存储路径和权限控制
    """

    def __init__(self, llm, skill_timeout: float = 300.0):
        self.llm = llm
        self.skill_timeout = skill_timeout
        self._skill_docs_cache: Dict[str, str] = {}

        # 初始化动态注册表
        self.registry = DynamicSkillRegistry(llm)

        # 注册所有内置技能
        for name, handler in SKILL_REGISTRY.items():
            manifest = SkillManifest(
                name=name,
                description=handler.__class__.__doc__ or "",
                runner="python_handler",
                keywords=_BUILTIN_KEYWORD_MAP.get(name, []),
            )
            self.registry.register_builtin(name, handler, manifest)

    # ================================================================
    # Phase 1: Skill Selection — 3-tier 匹配
    # ================================================================

    def _get_skills_description(
        self, state: DigitalSmartDoctorState, pool: Optional[List[str]] = None
    ) -> str:
        """获取可用技能的描述信息（从 DB + 注册表；pool 非空时仅描述池内技能）"""
        try:
            user_id = state.get("user_id", "")
            doctor_id = state.get("doctor_id", "")

            if pool:
                # 池内选择（worker 技能池）：只描述候选池内技能，避免 LLM 选池外技能
                available = self.registry.get_all_available(user_id)
                by_name = {s["name"]: s for s in available if s.get("name") in pool}
                desc = "可用技能:\n"
                for name in pool:
                    s = by_name.get(name)
                    desc += (
                        f"- {name}: {s.get('description', '')}\n"
                        if s else f"- {name}\n"
                    )
                return self._sanitize_text(desc)

            if user_id and doctor_id:
                from prompt.skills_prompt import get_skills_description

                desc = get_skills_description(user_id, doctor_id)
                return self._sanitize_text(desc)

            # 降级：从注册表获取
            available = self.registry.get_all_available(user_id)
            desc = "可用技能:\n"
            for s in available:
                desc += f"- {s['name']}: {s.get('description', '')}\n"
            return desc
        except Exception as e:
            logger.warning(f"获取技能描述失败，降级到内置列表: {e}")
            desc = "内置技能:\n"
            for name in self.registry.get_builtin_names():
                desc += f"- {name}\n"
            return desc

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """清理文本中的非法 UTF-8 surrogate 字符"""
        if not text:
            return text
        try:
            return text.encode("utf-8", errors="surrogatepass").decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return "".join(c for c in text if not (0xD800 <= ord(c) <= 0xDFFF))

    async def select_skills(
        self,
        human_input: str,
        state: DigitalSmartDoctorState,
        pool: Optional[List[str]] = None,
        agent_prompt: str = "",
    ) -> Optional[SkillSelectionResult]:
        """使用 LLM 结构化输出选择技能（Tier 3 降级）

        Args:
            human_input: 用户输入
            state: 当前状态
            pool: 候选技能名列表（限定 LLM 可选范围，子 agent 技能池用）；None=全库
            agent_prompt: 子 agent 的 system_prompt（注入身份/职责，让选择知其语境）

        Returns:
            SkillSelectionResult: LLM **真的做出了判定**（needs_skill 为 True/False 都算）
            None: LLM 调用失败，**没能做出判定**

        必须区分上面两种：改前失败时也构造 needs_skill=False 的对象，与"LLM 明确
        判定无需技能"在返回值上完全同形，调用方无法分辨，只能一律按同一种方式兜底
        （跑全池）。返回 None 让"没判定"成为调用方无法忽略的信号。
        """
        skills_description = self._get_skills_description(state, pool=pool)

        system_prompt = SKILL_SELECTION_SYSTEM_PROMPT.format(
            skills_description=skills_description
        )

        # 注入调用方（子 agent）的角色提示词：一次 LLM 调用内知晓"是谁在为谁选技能"
        messages = []
        if agent_prompt:
            messages.append(
                SystemMessage(content=self._sanitize_text(f"当前执行者：{agent_prompt}"))
            )
        messages.append(
            SystemMessage(content=self._sanitize_text(system_prompt))
        )
        messages.append(
            HumanMessage(content=self._sanitize_text(human_input))
        )

        try:
            structured_llm = self.llm.with_structured_output(SkillSelectionResult)
            result = await structured_llm.ainvoke(messages)
            if result.needs_skill and result.skills:
                logger.info(
                    f"[SkillSelect] LLM 选择: {[s.skill_name for s in result.skills]}"
                )
            return result
        except Exception as e:
            logger.error(f"技能选择 LLM 调用失败: {e}")
            # 返回 None（而不是捏造一个 needs_skill=False 的结果）：调用方必须
            # 能把"LLM 失败"和"LLM 判定无需技能"分开处理，见 select_skills_in_pool
            return None

    async def select_skills_in_pool(
        self,
        skill_pool: List[str],
        human_input: str,
        state: DigitalSmartDoctorState,
        agent_prompt: str = "",
    ) -> List[str]:
        """三级技能选择（限定候选池）—— multi_agent 下子 agent 执行技能前的技能选择。

        完整保留三级调度的"选择层"：
          Tier1+2: registry.match（关键词 + FTS5 确定性匹配，作用域 = pool）
          Tier3:   select_skills(pool=pool) LLM 池内选择（描述仅含池内技能 + agent_prompt 注入）
          兜底:    仅当「LLM 没能判定」或「LLM 说需要但给不出池内名字」时回退全量 pool
                   （LLM 明确判定无需技能时返回空，不执行任何技能）

        Args:
            skill_pool: 该子 agent 的技能池（DB skill_provider 启停 or 默认 self.skills）
            human_input: 用户输入
            state: 当前 Agent 状态（含 user_id）
            agent_prompt: 子 agent 的 system_prompt（透传 Tier3 LLM 选择）

        Returns:
            选中要执行的技能名列表（子集 or 全量兜底）
        """
        try:
            stream_writer = get_stream_writer()
        except Exception:
            stream_writer = None
        user_id = state.get("user_id", "")
        # 确保用户自定义技能已注册到注册表（此前只有旧 dispatch() 调用了该方法，
        # multi-agent 场景没加载 → 天气等自定义技能在 Tier1/2/3 都不可见）。幂等，~ms 级。
        if user_id:
            self._ensure_user_skills_loaded(user_id)
        pool = [s for s in (skill_pool or []) if s]
        if len(pool) <= 1:
            # 空池 / 单技能无需选择，直接执行
            return pool

        # Tier 1+2：确定性匹配 —— 作用域下沉到 match（匹配阶段即限定 pool）。
        # Fix: 原先 match 走全库再与 pool 取交集，池外技能的 Tier1 命中会误短路
        #      Tier2，使本可由 FTS5 召回的池内技能失去机会，白降级 Tier3(1.8s)。
        matched = self.registry.match(human_input, user_id, pool=pool)
        # 仍按 pool 顺序重投影：matched 已限定在 pool 内，此行只为保持返回/
        # 执行/结果拼接顺序稳定（不是"事后交集"，勿删）
        hits = [s for s in pool if s in matched]
        if hits:
            logger.info(f"[SkillSelect][T1/T2] 池内命中: {hits} (pool={pool})")
            self._emit_thought(
                stream_writer, f"🔍 技能三级调度：命中 {', '.join(hits)}"
            )
            return hits

        # Tier 3：LLM 池内选择（描述已限定 pool，避免选池外技能；注入调用方子 agent 角色）
        selection = await self.select_skills(human_input, state, pool=pool, agent_prompt=agent_prompt)

        # ---- Tier3 有三种结局，必须分开处理，不能一律兜底跑全池 ----
        # Fix: 改前 select_skills 失败时也返回 needs_skill=False，与"LLM 明确判定无需
        #      技能"不可区分，二者都落到「回退全量池」。典型后果：search_agent 被派了
        #      "制定健身计划"，池里 [web_search, clawhub_weather-cn] 无一对口（LLM 判
        #      无需技能），却仍把两个技能全跑一遍——白花一次联网调用，还把无关内容灌
        #      进 supervisor 的综合语料。
        if selection is None:
            # 结局 1：LLM 调用失败 = 没能做出判定 → 保守回退全量池（不丢功能）
            logger.warning(f"[SkillSelect][T3] LLM 调用失败，保守回退全量池: {pool}")
            self._emit_thought(stream_writer, "🔍 技能三级调度：LLM 异常 · 回退全量技能池")
            return pool

        if not selection.needs_skill:
            # 结局 2：LLM 明确判定「本轮不需要任何技能」→ 一个都不执行。
            # 这正是 Tier3 存在的意义：池里没有对口技能时，跑全池只会污染上下文。
            logger.info(f"[SkillSelect][T3] LLM 判定无需技能，跳过执行 (pool={pool})")
            self._emit_thought(stream_writer, "🔍 技能三级调度：LLM 判定本轮无需技能")
            return []

        hits = [s.skill_name for s in selection.skills if s.skill_name in pool]
        if hits:
            logger.info(f"[SkillSelect][T3] LLM 池内命中: {hits} (pool={pool})")
            self._emit_thought(
                stream_writer, f"🔍 技能三级调度：LLM 命中 {', '.join(hits)}"
            )
            return hits

        # 结局 3：LLM 说有技能需求、却给不出池内可用名字（输出错误/幻觉）→ 保守回退全量池。
        # 保留"不丢功能"语义：LLM 的判定是「需要技能」，不据此判空。
        logger.warning(
            f"[SkillSelect][T3] LLM 指定的技能均不在池内，回退全量池: "
            f"{[s.skill_name for s in selection.skills]} vs pool={pool}"
        )
        self._emit_thought(stream_writer, "🔍 技能三级调度：LLM 命中不可用 · 回退全量技能池")
        return pool

    # ================================================================
    # Phase 2: Skill Documentation — 通过注册表读取
    # ================================================================

    async def _read_skill_documentation(
        self, skill_name: str, state: DigitalSmartDoctorState
    ) -> Optional[str]:
        """从注册表读取 SKILL.md（带缓存）

        查找顺序：
        1. user_skills/{user_id}/{skill_name}/current/SKILL.md （用户自定义）
        2. .claude/skills/{skill_name}/SKILL.md （内置/全局）
        """
        user_id = state.get("user_id", "")

        # 先查注册表缓存
        doc = self.registry.get_skill_doc(skill_name, user_id)
        if doc:
            return doc

        # 降级：兼容旧 medical_record_no 路径
        medical_record_no = state.get("medical_record_no")
        if medical_record_no and medical_record_no != user_id:
            search_paths = [
                os.path.join(
                    "user_skills", medical_record_no, skill_name, "current", "SKILL.md"
                ),
                os.path.join(
                    ".claude", "skills", skill_name, "SKILL.md"
                ),
            ]
            for path in search_paths:
                abs_path = os.path.realpath(path)
                if os.path.exists(abs_path):
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                            content = self._sanitize_text(f.read())
                        self._skill_docs_cache[skill_name] = content
                        return content
                    except Exception as e:
                        logger.error(f"读取 SKILL.md 失败 {abs_path}: {e}")

        return None

    def _find_skill_base_dir(
        self, skill_name: str, state: DigitalSmartDoctorState
    ) -> Optional[str]:
        """查找技能的根目录（user_id 优先）"""
        user_id = state.get("user_id", "")
        medical_record_no = state.get("medical_record_no")

        candidates = []

        # user_id 路径（新架构）
        if user_id:
            candidates.append(
                os.path.join("user_skills", user_id, skill_name, "current")
            )
            candidates.append(
                os.path.join("user_skills", user_id, skill_name)
            )

        # medical_record_no 路径（向后兼容旧数据）
        if medical_record_no and medical_record_no != user_id:
            candidates.append(
                os.path.join("user_skills", medical_record_no, skill_name, "current")
            )

        # 全局路径
        candidates.append(os.path.join(".claude", "skills", skill_name))

        for path in candidates:
            abs_path = os.path.realpath(path)
            if os.path.isdir(abs_path):
                return abs_path

        return None

    # ================================================================
    # Phase 3: Skill Execution — 统一路由
    # ================================================================

    async def _execute_internal_skill(
        self,
        skill_name: str,
        state: DigitalSmartDoctorState,
        human_input: str,
    ) -> dict:
        """执行内置技能（通过 DynamicSkillRegistry）

        新架构：委托给 registry.execute() 统一处理。
        保留此方法作为直接 handler 调用的快速路径。
        """
        user_id = state.get("user_id", "")
        return await self.registry.execute(skill_name, state, human_input, user_id)

    async def _execute_custom_script(
        self,
        skill_name: str,
        state: DigitalSmartDoctorState,
        human_input: str,
    ) -> dict:
        """执行自定义脚本技能（直接执行，不走 LLM 工具循环）

        这是 v2 最大的性能改进。
        通过 execution.yaml / SKILL.md frontmatter 确定 entrypoint，
        直接 subprocess 执行脚本，延迟从 10-50s → 0.5-2s。
        """
        user_id = state.get("user_id", "")

        # 1. 尝试从注册表获取 manifest
        key = f"{user_id}:{skill_name}"
        manifest = self.registry._manifests.get(key)

        if not manifest:
            # 2. 尝试从文件系统构建 manifest
            base_dir = self._find_skill_base_dir(skill_name, state)
            if not base_dir:
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"技能目录不存在: {skill_name}",
                }

            # 3. 先尝试 execution.yaml
            exec_yaml = os.path.join(base_dir, "execution.yaml")
            if os.path.exists(exec_yaml):
                manifest = SkillManifest.from_yaml(exec_yaml)
            else:
                # 4. 降级：从 SKILL.md frontmatter 推断
                md_path = os.path.join(base_dir, "SKILL.md")
                manifest = SkillManifest.from_skill_md(md_path)
                if manifest:
                    # 自动发现 entrypoint：优先 scripts/ 目录
                    scripts_dir = os.path.join(base_dir, "scripts")
                    if os.path.isdir(scripts_dir):
                        for f in sorted(os.listdir(scripts_dir)):
                            if f.endswith((".py", ".sh")):
                                manifest.entrypoint = os.path.join("scripts", f)
                                break
                    if not manifest.entrypoint:
                        # 直接在根目录找
                        for f in sorted(os.listdir(base_dir)):
                            if f.endswith((".py", ".sh")) and f != "SKILL.md":
                                manifest.entrypoint = f
                                break

                if not manifest or not manifest.entrypoint:
                    # 5. 最终降级：使用 LLM 工具循环
                    logger.warning(
                        f"技能 {skill_name} 缺少 execution.yaml 且无法自动发现 entrypoint，"
                        f"降级到 LLM 工具循环"
                    )
                    return await self._execute_external_skill(
                        skill_name, human_input, state
                    )

            manifest.base_dir = base_dir
            # 缓存到注册表
            self.registry.register_custom(user_id, manifest)

        # 执行
        return await self.registry._execute_script(manifest, human_input, user_id)

    async def _execute_external_skill(
        self,
        skill_name: str,
        human_input: str,
        state: DigitalSmartDoctorState,
    ) -> dict:
        """LLM 工具循环执行（降级方案，仅用于无法自动发现 entrypoint 的复杂技能）"""
        try:
            skill_doc = await self._read_skill_documentation(skill_name, state)
            if not skill_doc:
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"无法读取技能文档: {skill_name}",
                }

            base_dir = self._find_skill_base_dir(skill_name, state)
            if not base_dir:
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"技能目录不存在: {skill_name}",
                }

            tools = make_skill_tools(base_dir)
            tools_by_name = {t.name: t for t in tools}

            system_content = EXECUTE_SYSTEM_INSTRUCTION.format(
                skill_doc=skill_doc,
                human_input=human_input,
            )
            messages = [
                SystemMessage(content=self._sanitize_text(system_content)),
                HumanMessage(content=self._sanitize_text(human_input)),
            ]
            llm_with_tools = self.llm.bind_tools(tools)

            max_iterations = 10
            response = None
            for iteration in range(max_iterations):
                response = await llm_with_tools.ainvoke(messages)

                tool_calls = getattr(response, "tool_calls", None)
                if not tool_calls:
                    logger.info(
                        f"外部技能 {skill_name} 工具循环结束（{iteration + 1} 轮）"
                    )
                    break

                messages.append(response)

                for tc in tool_calls:
                    tc_name = (
                        tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
                    )
                    tc_args = (
                        tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})
                    )
                    tc_id = (
                        tc["id"] if isinstance(tc, dict) else getattr(tc, "id", "")
                    )

                    tool_fn = tools_by_name.get(tc_name)
                    if tool_fn:
                        try:
                            tool_result = tool_fn.invoke(tc_args)
                        except Exception as te:
                            tool_result = f"[工具 {tc_name} 执行失败: {te}]"
                    else:
                        tool_result = f"[未知工具: {tc_name}]"

                    messages.append(
                        ToolMessage(content=str(tool_result), tool_call_id=tc_id)
                    )
            else:
                logger.warning(
                    f"外部技能 {skill_name} 达到最大轮数 {max_iterations}，强制结束"
                )

            final_content = (
                response.content if response and hasattr(response, "content") else ""
            ).strip()

            if not final_content:
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": "外部技能未返回有效内容",
                }

            return {
                "skill_name": skill_name,
                "success": True,
                "content": final_content,
            }

        except Exception as e:
            logger.exception(f"执行外部技能异常: {skill_name}")
            return {
                "skill_name": skill_name,
                "success": False,
                "content": f"执行异常: {str(e)}",
            }

    async def _execute_single_skill(
        self,
        skill_name: str,
        state: DigitalSmartDoctorState,
        human_input: str,
    ) -> dict:
        """执行单个技能（自动路由，带超时控制）

        v2 路由逻辑：
        1. 内置技能 (SKILL_REGISTRY) → Python handler
        2. 自定义技能 (有 execution.yaml / 可发现 entrypoint) → 直接执行脚本
        3. 复杂技能 (无法自动发现) → LLM 工具循环（降级）
        """
        try:
            return await asyncio.wait_for(
                self._execute_skill_core(skill_name, state, human_input),
                timeout=self.skill_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"技能 {skill_name} 执行超时（{self.skill_timeout}秒）")
            return {
                "skill_name": skill_name,
                "success": False,
                "content": f"执行超时（{self.skill_timeout}秒）",
            }

    async def _execute_skill_core(
        self,
        skill_name: str,
        state: DigitalSmartDoctorState,
        human_input: str,
    ) -> dict:
        """技能执行核心：智能路由

        v2 改进：
        - 内置技能 → Python handler（不变）
        - 自定义技能 → 优先直接执行脚本（新增），降级到 LLM 工具循环
        """
        # 内置技能
        if skill_name in SKILL_REGISTRY:
            return await self._execute_internal_skill(skill_name, state, human_input)

        # 自定义技能：根据 runner 类型路由
        user_id = state.get("user_id", "")
        if self.registry.is_registered(skill_name, user_id):
            # 检查 manifest 的 runner 类型
            key = f"{user_id}:{skill_name}"
            manifest = self.registry._manifests.get(key)
            if manifest and manifest.runner == "llm_tool_loop":
                # LLM 工具循环：由 dispatcher 亲自执行（有 LLM 实例和完整上下文）
                return await self._execute_external_skill(
                    skill_name, human_input, state
                )
            # subprocess_script / python_handler：委托给注册表执行
            return await self.registry.execute(skill_name, state, human_input, user_id)

        # 未注册的自定义技能：尝试从文件系统加载 manifest
        base_dir = self._find_skill_base_dir(skill_name, state)
        if base_dir:
            exec_yaml = os.path.join(base_dir, "execution.yaml")
            if os.path.exists(exec_yaml):
                return await self._execute_custom_script(
                    skill_name, state, human_input
                )

        # 最终降级：LLM 工具循环
        return await self._execute_external_skill(skill_name, human_input, state)

    # ================================================================
    # Phase 4: Result Processing
    # ================================================================

    def _process_results(
        self, results: List, skill_names: List[str]
    ) -> tuple:
        """处理所有技能执行结果"""
        successful_results = []
        failed_results = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"{skill_names[i]} 抛出异常: {result}")
                failed_results.append(f"{skill_names[i]}: 执行异常 - {str(result)}")
            elif result.get("success"):
                successful_results.append(
                    f"**{result['skill_name']}** 查询结果:\n{result['content']}"
                )
            elif not result.get("silent"):
                failed_results.append(f"{result['skill_name']}: {result['content']}")

        return successful_results, failed_results

    # ================================================================
    # 思考过程推送（Thinking Reveal）：把调度/执行过程实时推给前端
    # ================================================================
    _THOUGHT_SUMMARY_MAX = 120  # 单条思考摘要最大字符数，防止长结果刷屏

    @staticmethod
    def _thought_summary(text: Optional[str], max_len: int = _THOUGHT_SUMMARY_MAX) -> str:
        """把技能执行结果压成一行摘要：去换行/空白，超长截断加省略号"""
        if not text:
            return ""
        one_line = " ".join(str(text).split())
        return one_line[:max_len] + ("…" if len(one_line) > max_len else "")

    @staticmethod
    def _emit_thought(stream_writer, text: str) -> None:
        """向 langgraph custom 流写入一条思考过程事件（writer 不可用/非图内时静默跳过）"""
        if stream_writer is not None:
            stream_writer({"type": "thought", "content": text})

    # ================================================================
    # 前置条件过滤（Fix: 技能误调度）
    # ================================================================
    _IMAGE_EXT_RE = re.compile(r"[\w\-./\\:]+\.(?:jpg|jpeg|png|webp|bmp|gif)", re.I)

    def _existing_image_inputs(
        self, state: DigitalSmartDoctorState, human_input: str
    ) -> List[str]:
        """收集真实存在的图片路径：state 直带字段 + sub_agent_input JSON + human_input 内嵌"""
        candidates: List[str] = []
        # 1) state 直带字段
        for k in ("image_path", "img_path"):
            if state.get(k):
                candidates.append(str(state[k]))
        # 2) sub_agent_input 里的 JSON（image_process 上游写入过 image_path）
        sub = state.get("sub_agent_input")
        if isinstance(sub, str):
            try:
                data = json.loads(sub)
                if isinstance(data, dict) and data.get("image_path"):
                    candidates.append(str(data["image_path"]))
            except (json.JSONDecodeError, TypeError):
                pass
        # 3) human_input 内嵌路径（img_skill 分支会拼成 "路径+帮我识别一下这张图片"）
        candidates += self._IMAGE_EXT_RE.findall(human_input or "")
        return [p for p in candidates if os.path.exists(p)]

    def _prune_skills_by_prerequisites(
        self,
        skill_names: List[str],
        state: DigitalSmartDoctorState,
        human_input: str,
    ) -> List[str]:
        """剔除当前不满足执行前置条件的技能（如 derma_image 无真实图片时）

        Fix: 就诊准备/检查建议类查询会被 FTS5 顺带召回 derma_image，
        但该技能没有图片输入根本无法执行，直接跳过以减少无效 LLM 调用与失败日志。
        """
        pruned: List[str] = []
        for name in skill_names:
            handler = SKILL_REGISTRY.get(name)
            requires_image = getattr(handler, "requires_image", False)
            if requires_image and not self._existing_image_inputs(state, human_input):
                logger.info(f"[Dispatch] 跳过技能 {name}（缺少图片路径前置条件）")
            else:
                pruned.append(name)
        return pruned

    # ================================================================
    # Main Entry Point
    # ================================================================

    async def dispatch(self, state: DigitalSmartDoctorState) -> dict:
        """统一技能调度入口 (v2)

        流程：
        1. Tier 1+2: 关键词匹配 / FTS5 语义检索
        2. Tier 3（降级）: LLM 结构化输出
        3. 并行执行所有匹配到的技能
        4. 结果聚合

        Args:
            state: 当前 Agent 状态

        Returns:
            状态更新字典
        """
        human_input = str(state["human_input"])
        user_id = state.get("user_id", "")

        # 获取 langgraph custom 流 writer（chat_node 同款），把「思考过程」实时推给前端。
        # try 包裹：dispatch 若被非 graph 上下文直接调用（如单测），静默降级为不发事件。
        try:
            stream_writer = get_stream_writer()
        except Exception:
            stream_writer = None

        # 兼容原有 image_process 流程
        def get_skill_name(s):
            try:
                data = json.loads(s.get("sub_agent_input", "{}"))
                return data.get("skill_name")
            except (json.JSONDecodeError, TypeError, AttributeError):
                return None

        img_skill = get_skill_name(state)
        if img_skill:
            image_data = json.loads(state.get("sub_agent_input", "{}"))
            human_input = image_data.get("image_path", "") + "帮我识别一下这张图片"

        # Phase 1: 技能选择（3-tier 匹配）
        if img_skill:
            skill_names = [img_skill]
        else:
            # 确保用户自定义技能已加载（必须在 match 之前）
            if user_id:
                self._ensure_user_skills_loaded(user_id)

            # Tier 1+2: 关键词/FTS5 匹配
            skill_names = self.registry.match(human_input, user_id)

            if not skill_names:
                # Tier 3: LLM 降级
                selection = await self.select_skills(human_input, state)
                # None = LLM 调用失败（没能判定）。单 agent 路径没有"候选池"可回退，
                # 跑全库技能风险更大（会执行一堆无关的内置技能），故与"判定无需技能"
                # 同样返回 {}，保持改前行为不变。
                if selection is None or not selection.needs_skill or not selection.skills:
                    logger.info(
                        "无需技能调用" if selection is not None
                        else "技能选择 LLM 失败，跳过技能"
                    )
                    return {}
                skill_names = [item.skill_name for item in selection.skills]

        logger.info(f"[Dispatch] 调度技能: {skill_names} (user={user_id})")

        # 前置条件过滤：无真实图片时不调度 derma_image 等依赖图片的技能（Fix: 技能误调度）
        skill_names = self._prune_skills_by_prerequisites(skill_names, state, human_input)
        if not skill_names:
            logger.info("所有技能均不满足前置条件，取消调度")
            return {}

        # 思考过程：告知前端本轮正在调用哪些技能
        self._emit_thought(stream_writer, f"📋 正在调用技能：{'、'.join(skill_names)}")

        # Phase 2: 并行执行所有技能
        tasks = [
            self._execute_single_skill(name, state, human_input)
            for name in skill_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Phase 3: 结果处理
        successful_results, failed_results = self._process_results(
            results, skill_names
        )

        # 思考过程：逐技能推送结果摘要（成功 ✅ / 失败 ⚠️，静默项如无病历号不打扰用户）
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self._emit_thought(stream_writer, f"⚠️ {skill_names[i]}：执行异常")
            elif result.get("silent"):
                continue
            elif result.get("success"):
                self._emit_thought(
                    stream_writer,
                    f"✅ {result['skill_name']}：{self._thought_summary(result['content'])}",
                )
            else:
                self._emit_thought(
                    stream_writer,
                    f"⚠️ {result['skill_name']}：{self._thought_summary(result['content']) or '执行失败'}",
                )

        if not successful_results:
            if failed_results:
                logger.warning(f"所有技能执行失败: {failed_results}")
            return {}

        combined_result = "\n\n---\n\n".join(successful_results)

        if failed_results:
            logger.warning(f"部分技能执行失败: {failed_results}")

        return {
            "sub_agent_input": combined_result,
            "human_input": human_input,
        }

    def _ensure_user_skills_loaded(self, user_id: str):
        """确保用户的自定义技能已加载到注册表（按需加载）"""
        if self.registry.get_custom_names(user_id):
            return  # 已加载

        # 从文件系统扫描用户自定义技能
        user_skills_dir = os.path.join("user_skills", user_id)
        if not os.path.isdir(user_skills_dir):
            return

        for skill_name in sorted(os.listdir(user_skills_dir)):
            skill_dir = os.path.join(user_skills_dir, skill_name)
            if not os.path.isdir(skill_dir):
                continue

            # 优先找 current/ 子目录
            current_dir = os.path.join(skill_dir, "current")
            if os.path.isdir(current_dir):
                skill_dir = current_dir

            manifest = None

            # 尝试 execution.yaml
            exec_yaml = os.path.join(skill_dir, "execution.yaml")
            if os.path.exists(exec_yaml):
                manifest = SkillManifest.from_yaml(exec_yaml)
            else:
                # 降级：从 SKILL.md 推断
                md_path = os.path.join(skill_dir, "SKILL.md")
                if os.path.exists(md_path):
                    manifest = SkillManifest.from_skill_md(md_path)
                    if manifest:
                        # 自动发现 entrypoint
                        scripts_dir = os.path.join(skill_dir, "scripts")
                        if os.path.isdir(scripts_dir):
                            for f in sorted(os.listdir(scripts_dir)):
                                if f.endswith((".py", ".sh")):
                                    manifest.entrypoint = os.path.join("scripts", f)
                                    break
                        if not manifest.entrypoint:
                            for f in sorted(os.listdir(skill_dir)):
                                if f.endswith((".py", ".sh")) and f != "SKILL.md":
                                    manifest.entrypoint = f
                                    break

            if manifest:
                manifest.base_dir = os.path.realpath(skill_dir)
                self.registry.register_custom(user_id, manifest)
                logger.debug(
                    f"[Dispatch] 按需加载技能: {user_id}:{manifest.name}"
                )
