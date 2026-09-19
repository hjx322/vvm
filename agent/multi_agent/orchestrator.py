"""多 Agent 编排器（计划 P2，"单节点内部编排"）

LangGraph 运行时仍是"同一时刻一个节点"，但节点内部完成：
  图片快路径 → supervisor 规划 → L1 数据 agent 并行 → L3 safety 门卫
  → supervisor 合规综合（诊断聚合已并入 supervisor 综合）。
未来升级真子图只需把 asyncio.gather 换成 Send API fan-out，state 字段不变。
"""

import asyncio
import json
import re

from loguru import logger
from langgraph.config import get_stream_writer

from config.app_config import configs
from agent.core.state import DigitalSmartDoctorState
from agent.multi_agent.schemas import SupervisorPlan, AgentTask, AgentResult
from agent.multi_agent.supervisor import Supervisor

# 合并后的患者+知识子 agent 名（原 patient_worker / knowledge_worker 合二为一 → agent 命名）
_PATIENT_AGENT = "patient_knowledge_agent"

# 通用助手（闲聊/常识/资讯/天气等非医疗或混合问题，可用联网/通用技能）
_GENERAL_AGENT = "general_agent"

# L1 数据 agent 集合：可并行 fan-out 的唯一一层
# （patient_knowledge 内部 mysql/milvus 技能级并行，外部与 search/general 仍是 agent 级并行）
_L1_DATA_AGENTS = frozenset({_PATIENT_AGENT, "search_agent", _GENERAL_AGENT})

# 旧名归一映射（worker→agent 全量）：防 supervisor LLM 偶发沿用旧名（旧 *_worker 名
# 与合并前的 patient_worker/knowledge_worker），归一后保序去重
_LEGACY_AGENT_ALIASES = {
    "patient_worker": _PATIENT_AGENT,
    "knowledge_worker": _PATIENT_AGENT,
    "patient_knowledge_worker": _PATIENT_AGENT,
    "search_worker": "search_agent",
    "imaging_worker": "imaging_agent",
    "safety_worker": "safety_agent",
}


def _normalize_agent_names(names) -> list:
    """把 plan 里的 agent 名归一化：旧名映射到新名，并保序去重"""
    out: list = []
    for n in names:
        n2 = _LEGACY_AGENT_ALIASES.get(n, n)
        if n2 not in out:
            out.append(n2)
    return out

# L3 兜底：药名/剂量 regex（双保险——结构化 SafetyVerdict 判定之外，
# 只需识别"需补免责声明的用法表述"，不再用于拒绝回答）
_PRESCRIPTION_RE = re.compile(
    r"("
    r"卡泊三醇|甲氨蝶呤|阿莫西林|米诺地尔|维A酸|维甲酸|阿达帕林|异维A酸|"
    r"伊曲康唑|特比萘芬|氟康唑|红霉素软膏|他克莫司|"
    r"\d+\s?(mg|克|毫升)\s?(每次|每日|每天|/日|bid|tid)?|"
    r"[0-9]\s?次\s?/?日"
    r")"
)

# L3 免责声明：涉及用药/危险内容时统一追加的兜底话术（政策：可答，但危险时附仅供参考）
DISCLAIMER_MEDICAL_ADVICE = "以上信息仅供参考，具体用药请遵医嘱；如症状加重或出现危急情况，请及时就医。"


class MultiAgentOrchestrator:
    """主管-工人编排器"""

    def __init__(self, registry, supervisor: Supervisor = None, dispatcher=None):
        self.registry = registry
        self.supervisor = supervisor or Supervisor()
        self.dispatcher = dispatcher

    @staticmethod
    def _extract_img_skill(state: dict) -> dict:
        """从 state.sub_agent_input 提取图片技能（image_process 写入 {skill_name, image_path}）"""
        raw = state.get("sub_agent_input")
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return None
        else:
            data = raw
        if isinstance(data, dict) and data.get("skill_name"):
            return data
        return None

    async def _image_fast_path(self, state, img_skill: dict) -> dict:
        """图片快路径：免 plan，直接执行 derma_image 并综合"""
        writer = self._stream()
        skill_name = img_skill["skill_name"]
        human_input = str(img_skill.get("image_path", "")) + "帮我识别一下这张图片"
        res = await self.dispatcher._execute_single_skill(skill_name, state, human_input)
        ok, failed = self.dispatcher._process_results([res], [skill_name])
        outputs = {
            "imaging_agent": (
                "；".join(ok) or ("；".join(failed) if failed else "图片检测无结果")
            )
        }
        status = "success" if ok else "failed"
        self._emit(writer, f"🩺 imaging_agent 完成（{'✅' if ok else '⚠️'}）")
        if outputs.get("imaging_agent"):
            self._emit_worker_result(writer, "imaging_agent", outputs["imaging_agent"], prefix="🩺")
        reply = await self.supervisor.synthesize(state, worker_outputs=outputs)
        self.supervisor.stream_output(reply)
        return {
            "worker_outputs": outputs,
            "worker_status": {"imaging_agent": status},
            "agent_plan": None,
            "active_workers": ["imaging_agent"],
            "final_answer": reply,
        }

    async def execute(self, state: DigitalSmartDoctorState) -> dict:
        """编排主流程：作为主图 node_multi_agent 节点返回 state 更新片段

        期间用 stream_logger.bridge 把 loguru INFO 日志桥接到前端思考过程，
        让用户看到后端各模块实际运行的内容（MySQL 连接、进度、检索结果等）。
        """
        writer = self._stream()
        # 思考过程只保留 orchestrator 的阶段性提示（规划/调用/完成/安全提示），
        # 不再把技能内部日志（Tavily 逐条搜索日志、milvus/MySQL 细节）桥接进前端，
        # 避免前端思考区被原始日志/文档全文刷屏（细节日志仍输出到后端日志文件）。
        return await self._execute_with_stream(state, writer)

    async def _execute_with_stream(self, state, writer) -> dict:
        """编排主体（writer 由 execute 传入，供 _emit/bridge 复用）"""
        update = {
            "worker_outputs": {},
            "worker_status": {},
            "agent_plan": None,
            "active_workers": [],
        }

        # 1) 图片快路径（sub_agent_input 带 skill_name → 免 plan）
        img_skill = self._extract_img_skill(state)
        if img_skill:
            return await self._image_fast_path(state, img_skill)

        # 2) supervisor 规划（失败降级为纯闲聊直达）
        try:
            # 动态生成子 agent 能力描述（含各 agent 当前启用技能 + 任务要点），让规划感知启停
            agent_descriptions = await self.registry.build_descriptions()
            plan = await self.supervisor.plan(state, agent_descriptions=agent_descriptions)
        except Exception as e:
            logger.exception(f"supervisor plan 失败，降级: {e}")
            plan = SupervisorPlan(
                needs_workers=False,
                direct_reply="抱歉，我暂时无法处理这个问题，请稍后再试。",
            )

        update["agent_plan"] = plan.model_dump()
        self._emit(
            writer,
            f"🧠 规划：{plan.task_summary or ('无需子 agent' if not plan.needs_workers else '、'.join(plan.worker_names))}",
        )

        # 3) 无子 agent（仅纯寒暄：supervisor 判定无任何可检索实体）：
        #    先给 general_agent 一次低成本机会（关键词/FTS5 试探其技能池，命中才执行，
        #    未命中零 LLM），实现"闲聊也能调用 skill"；仍未命中才直达合规综合。
        if not plan.needs_workers:
            gr = await self._probe_general_agent(state)
            if gr is not None and (gr.success or gr.content):
                wo = {_GENERAL_AGENT: gr.content}
                update["active_workers"] = [_GENERAL_AGENT]
                update["worker_outputs"] = wo
                update["worker_status"] = {_GENERAL_AGENT: "success" if gr.success else "failed"}
                reply = await self.supervisor.synthesize(state, worker_outputs=wo)
                self.supervisor.stream_output(reply)
                update["final_answer"] = reply
                return update
            update["active_workers"] = []
            reply = await self.supervisor.synthesize(state, direct_reply=plan.direct_reply)
            self.supervisor.stream_output(reply)
            update["final_answer"] = reply
            return update

        registered = set(self.registry.get_names())
        raw_names = _normalize_agent_names(plan.worker_names)
        # 候选：已注册 且 属于 L1 数据 agent → 可并行
        runnable = [w for w in raw_names if w in registered and w in _L1_DATA_AGENTS]
        unknown = [w for w in raw_names if w not in registered]
        if unknown:
            logger.warning(f"plan 选择了未注册子 agent: {unknown}")

        # 强制补充 patient_knowledge_agent —— supervisor 可能漏选，导致回答缺患者既往史。
        # 条件：配置开启 且 已注册 且 plan 未选；（仅 general_agent 的纯闲聊轮不强查病历）
        force_patient = (
            getattr(configs.multi_agent, "always_query_patient", True)
            and _PATIENT_AGENT in registered
            and _PATIENT_AGENT not in runnable
            and set(runnable) != {_GENERAL_AGENT}
        )
        if force_patient:
            logger.info(f"[MultiAgent] 强制补充 {_PATIENT_AGENT}：每轮查询患者病历")
            runnable.append(_PATIENT_AGENT)

        # active_workers：含 task 调度的全部子 agent + 可能追加的 safety
        active = list(raw_names)
        if force_patient:
            active.append(_PATIENT_AGENT)
        if plan.needs_medication_safety:
            active.append("safety_agent")
        update["active_workers"] = active

        status: dict = {}
        outputs: dict = {}
        history_updates = []

        # 4) L1 数据子 agent 并行执行（gather）；force 轮给合并 agent 传 force_patient，
        #    只查病歷（跳过可池 milvus_query），避免天气等轮次多检索知识库
        if runnable:
            self._emit(writer, f"📋 正在调用子 agent：{'、'.join(runnable)}")
            results = await asyncio.gather(*[
                self._run_agent(
                    w, state, payload={"force_patient": force_patient and w == _PATIENT_AGENT}
                )
                for w in runnable
            ], return_exceptions=True)  # 双保险：_run_agent 已就地兜底，防意外异常取消同轮其它 agent
            for w, res in zip(runnable, results):
                if isinstance(res, Exception):
                    logger.error(f"[MultiAgent] 子 agent {w} 未捕获异常: {res}")
                    outputs[w] = f"{w} 执行异常: {res}"
                    status[w] = "failed"
                    continue
                if res is None:
                    continue
                if res.history_update:
                    history_updates.append(res.history_update)
                if res.skipped:
                    # 本轮无技能可执行（LLM 判定无需技能 / 池空 / 被前置条件剪枝）：
                    # 这是正常结局而非失败。不写 worker_outputs，避免"未查询到结果"
                    # 这个占位串混进 supervisor 的综合语料。
                    status[res.name] = "skipped"
                    continue
                outputs[res.name] = res.content
                status[res.name] = "success" if res.success else "failed"
                # 把子 agent 实际查询到的内容（如 web_search 的天气结果）推给前端作为思考块，
                # 与后端日志对齐，让用户看到"模型真查到了什么"
                if res.content:
                    self._emit_worker_result(writer, res.name, res.content)
            for w in runnable:
                st = status.get(w)
                if st == "success":
                    self._emit(writer, f"✅ {w} 完成")
                elif st == "skipped":
                    self._emit(writer, f"⏭️ {w} 本轮无需技能，跳过")
                else:
                    self._emit(writer, f"⚠️ {w} 结果异常")
        else:
            self._emit(writer, "📋 本轮无可执行数据子 agent，直接综合")

        # 5) （原 diagnosis_worker 已移除：诊断聚合职责并入 supervisor 综合，
        #     supervisor.synthesize 直接基于 L1 worker_outputs 形成鉴别诊断 + 合规回答）

        # 6) 推理子 agent 独立记忆深合并进 update
        for hu in history_updates:
            for key, val in hu.items():
                merged = dict(update.get(key) or {})
                if isinstance(val, dict):
                    merged.update(val)
                update[key] = merged

        update["worker_outputs"] = outputs
        update["worker_status"] = status

        # 7) supervisor 综合生成合规草稿（非流式）
        # 诊断聚合已并入 supervisor 综合：直接基于 L1 子 agent 结果 outputs 生成
        draft = await self.supervisor.synthesize(
            state, worker_outputs=outputs
        )

        # 8) L3 安全门卫：涉及用药/急症时 safety agent 复核，
        #    发现未免责时在原文后追加"仅供参考/及时就医"提醒（不再替换为固定拒绝句）
        blocked = False
        violations: list = []
        if plan.needs_medication_safety and self.registry.get("safety_agent"):
            sres = await self._run_agent("safety_agent", state, human_input=draft, payload={})
            if sres is not None:
                update["worker_status"]["safety_agent"] = "success" if sres.success else "failed"
                update["worker_outputs"]["safety_agent"] = sres.content
                # 结构化判定（SafetyVerdict → content 以"违规"开头）+ regex 兜底双保险
                if sres.success and sres.content.startswith("违规"):
                    blocked = True
                    raw = sres.content[2:].lstrip("：;；，")
                    violations = [v.strip() for v in raw.split("；") if v.strip()] if raw else []
                elif _PRESCRIPTION_RE.search(draft):
                    blocked = True
                    violations = _PRESCRIPTION_RE.findall(draft)
                if blocked:
                    if "仅供参考" in draft:
                        # draft 已由 supervisor 自带免责声明，幂等跳过，避免重复叠加
                        self._emit(writer, "🛡️ safety_agent 复核通过（回答已含'仅供参考'免责声明）")
                    else:
                        self._emit(writer, "🛡️ safety_agent 提示：检测到用药/剂量内容，已追加'仅供参考'免责声明")
                        draft = draft.rstrip() + "\n\n" + DISCLAIMER_MEDICAL_ADVICE
                else:
                    self._emit(writer, "🛡️ safety_agent 复核通过")
        update["safety_verdict"] = {
            "blocked": blocked,
            "violations": violations,
            "worker": "safety_agent" if self.registry.get("safety_agent") else None,
        }

        # 9) 只输出已过门卫的最终文本 + 写回 final_answer
        self.supervisor.stream_output(draft)
        update["final_answer"] = draft
        return update

    async def _run_agent(self, name: str, state, human_input=None, payload=None):
        """按名分发任务给子 agent。

        局部上下文投影：子 agent 声明 context_fields 白名单时，只把职责字段从全量 state
        抽出再传给 agent.run（标准①：每个 agent 只保留自己职责相关的局部上下文，
        避免全局键如 worker_outputs/agent_plan/final_answer 被无关 agent 看到）。
        未声明（context_fields=None）保持透传全量，兼容 search/imaging/safety/general。
        """
        agent = self.registry.get(name)
        if agent is None:
            return None
        task = AgentTask(
            name=name,
            human_input=human_input or str(state.get("human_input", "")),
            skill_names=list(agent.skills),
            payload=payload or {},
        )
        wstate = state
        fields = getattr(agent, "context_fields", None)
        if fields:
            wstate = {k: v for k, v in dict(state).items() if k in fields}
            # 兜底：human_input 是技能执行必要入参，白名单缺失时也保留
            wstate.setdefault("human_input", task.human_input)
            logger.debug(
                f"[AgentCtx] {name} 局部上下文: {sorted(wstate.keys())}"
            )
        # Fix: 单个子 agent 抛异常不再向上冒泡。原先 gather 缺 return_exceptions，
        # 一个 agent 失败会取消同轮其它 agent 并让整轮编排崩掉（用户拿到 500）。
        # 现改为就地吞掉并返回失败 AgentResult，本轮其余 agent 结果照常聚合。
        try:
            return await agent.run(task, wstate)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[MultiAgent] 子 agent {name} 执行异常，标记为失败: {e}")
            return AgentResult(name=name, success=False, content=f"{name} 执行异常: {e}")

    async def _probe_general_agent(self, state):
        """needs_workers=False 轮次给 general_agent 一次低成本机会：
        Tier1/2（关键词+FTS5，0-10ms）命中其技能池才执行；未命中返回 None → 纯寒暄直接回复（零额外 LLM）。
        作用域 = 其技能池：池外技能的命中不影响该判定（否则会被误判为"无需执行"）。

        实现"闲聊也能调用 skill"：纯寒暄也能由通用 agent 用天气/资讯等技能取数。
        """
        if not self.registry.get(_GENERAL_AGENT):
            return None
        agent = self.registry.get(_GENERAL_AGENT)
        effective = list(agent.skills)
        if agent.skill_provider is not None:
            db_skills = await agent.skill_provider.get_enabled_skill_names(_GENERAL_AGENT)
            if db_skills is not None:
                effective = db_skills
        if not effective:
            return None
        human = str(state.get("human_input", ""))
        user_id = state.get("user_id", "") or ""
        if self.dispatcher is None:
            return None
        if user_id:
            # 自定义技能（weather 等）按需加载进注册表，Tier1/2 才见得到
            self.dispatcher._ensure_user_skills_loaded(user_id)
        # 作用域下沉到 match：全库命中若全在池外，不再被误判为"无需执行"
        matched = self.dispatcher.registry.match(human, user_id, pool=effective)
        if not matched:
            return None
        return await self._run_agent(_GENERAL_AGENT, state, payload={})

    @staticmethod
    def _stream():
        try:
            return get_stream_writer()
        except Exception:
            return None

    @staticmethod
    def _emit(writer, text: str) -> None:
        """推思考过程给前端（LangGraph custom 流，type=thought）"""
        if writer:
            writer({"type": "thought", "content": text})

    @staticmethod
    def _brief_result(content: str, max_len: int = 90) -> str:
        """从子 agent 检索结果里抽一行有信息量的内容作为前端摘要（不整段贴原始数据）。

        策略：先跳过头尾元数据行（搜索关键词/来源/时间、=分栏线、collection/score/序号等），
        然后优先返回**包含天气/患者字段的行**（如"最高温度 31°C""病历号 1881921…"），
        找不到信息行时回退到第一条实质行，再回退到前面 max_len 字符。
        """
        _meta_prefixes = (
            "搜索关键词", "搜索来源", "返回结果数", "搜索时间", "======",
            "collection", "第一阶段", "第二阶段(", "score:", "_id:", "metadata", "text:",
            "query:", "重排", "**", "查询结果", "[", "相关性",
        )
        _info_kw = (
            "℃", "温度", "温度", "湿度", "阵雨", "阵雨", "多云", "晴", "雨",
            "天气", "天氣", "病历号", "姓名", "电话", "生日", "疾病", "病名",
        )

        def _clean(row):
            return " ".join(row.split())

        lines = [l.strip() for l in content.splitlines()]
        # 第一轮：抓"信息量"行（天气/患者/疾病）
        for line in lines:
            if not line or line.startswith(_meta_prefixes):
                continue
            if any(k in line for k in _info_kw):
                one = _clean(line)
                return one if len(one) <= max_len else one[:max_len] + "…"
        # 第二轮：回退到第一条实质行
        for line in lines:
            if line and not line.startswith(_meta_prefixes):
                one = _clean(line)
                return one if len(one) <= max_len else one[:max_len] + "…"
        return " ".join(content.split())[:max_len]

    @staticmethod
    def _emit_worker_result(writer, worker_name: str, content: str, prefix: str = "📊") -> None:
        """推一行检索结果摘要给前端思考块（不再贴整个查询全文，避免格式杂乱）"""
        if not writer or not content:
            return
        brief = MultiAgentOrchestrator._brief_result(content)
        writer({"type": "thought", "content": f"{prefix} {worker_name}：{brief}"})

    # ==================== 子图节点形态（P1 升级：多智能体在图上可见） ====================
    # 把 execute()/_execute_with_stream() 的"单节点黑盒编排"拆成 LangGraph 子图节点：
    # 规划 → L1 子 agent 并行 → 综合 → 安全门卫，每一环都成为图上独立、可单点调试的节点。
    # 业务逻辑与最终结果和旧编排完全一致；execute() 保留为兼容入口。
    # 并行暂用 asyncio.gather（LangGraph 运行时单节点并发），未来可升级 Send API fan-out。

    async def node_plan(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·规划：检测图片快路径；否则 supervisor.plan（失败降级纯闲聊）"""
        # 图片快路径：免 plan，交给 image_fast_node 直接执行（audit 字段保持与原 image 分支一致）
        img_skill = self._extract_img_skill(state)
        if img_skill:
            return {
                "agent_plan": None,
                "worker_outputs": {},
                "worker_status": {},
                "active_workers": ["imaging_agent"],
            }
        try:
            # 动态生成子 agent 能力描述（含各 agent 当前启用技能 + 任务要点），让规划感知启停
            agent_descriptions = await self.registry.build_descriptions()
            plan = await self.supervisor.plan(state, agent_descriptions=agent_descriptions)
        except Exception as e:
            logger.exception(f"supervisor plan 失败，降级: {e}")
            plan = SupervisorPlan(
                needs_workers=False,
                direct_reply="抱歉，我暂时无法处理这个问题，请稍后再试。",
            )
        self._emit(
            self._stream(),
            f"🧠 规划：{plan.task_summary or ('无需子 agent' if not plan.needs_workers else '、'.join(plan.worker_names))}",
        )
        return {"agent_plan": plan.model_dump(), "active_workers": []}

    @staticmethod
    def route_after_plan(state: DigitalSmartDoctorState) -> str:
        """子图条件路由：图片快路径 / 纯闲聊直达综合 / 正常子 agent 并行"""
        if MultiAgentOrchestrator._extract_img_skill(state):
            return "image_fast_node"
        plan = state.get("agent_plan")
        if isinstance(plan, dict) and not plan.get("needs_workers", True):
            return "direct_reply_node"
        return "workers_parallel_node"

    async def node_workers(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·L1 数据子 agent 并行执行（asyncio.gather，保持现并行语义）"""
        plan = SupervisorPlan.model_validate(state.get("agent_plan") or {})
        writer = self._stream()
        registered = set(self.registry.get_names())
        raw_names = _normalize_agent_names(plan.worker_names)
        # 候选：已注册 且 属于 L1 数据 agent → 可并行
        runnable = [w for w in raw_names if w in registered and w in _L1_DATA_AGENTS]
        unknown = [w for w in raw_names if w not in registered]
        if unknown:
            logger.warning(f"plan 选择了未注册子 agent: {unknown}")

        # 强制补充 patient_knowledge_agent —— supervisor 可能漏选，导致回答缺患者既往史
        force_patient = (
            getattr(configs.multi_agent, "always_query_patient", True)
            and _PATIENT_AGENT in registered
            and _PATIENT_AGENT not in runnable
            and set(runnable) != {_GENERAL_AGENT}
        )
        if force_patient:
            logger.info(f"[MultiAgent] 强制补充 {_PATIENT_AGENT}：每轮查询患者病历")
            runnable.append(_PATIENT_AGENT)

        # active_workers：含 task 调度的全部子 agent + 可能追加的 safety
        active = list(raw_names)
        if force_patient:
            active.append(_PATIENT_AGENT)
        if plan.needs_medication_safety:
            active.append("safety_agent")

        status: dict = {}
        outputs: dict = {}
        history_updates = []

        # L1 数据子 agent 并行执行（gather）；force 轮只查病歷（合并 agent 跳过可池 milvus）
        if runnable:
            self._emit(writer, f"📋 正在调用子 agent：{'、'.join(runnable)}")
            results = await asyncio.gather(*[
                self._run_agent(
                    w, state, payload={"force_patient": force_patient and w == _PATIENT_AGENT}
                )
                for w in runnable
            ], return_exceptions=True)  # 双保险：_run_agent 已就地兜底，防意外异常取消同轮其它 agent
            for w, res in zip(runnable, results):
                if isinstance(res, Exception):
                    logger.error(f"[MultiAgent] 子 agent {w} 未捕获异常: {res}")
                    outputs[w] = f"{w} 执行异常: {res}"
                    status[w] = "failed"
                    continue
                if res is None:
                    continue
                if res.history_update:
                    history_updates.append(res.history_update)
                if res.skipped:
                    # 本轮无技能可执行（LLM 判定无需技能 / 池空 / 被前置条件剪枝）：
                    # 正常结局而非失败，不写 worker_outputs（见 _execute_with_stream 同名分支）
                    status[res.name] = "skipped"
                    continue
                outputs[res.name] = res.content
                status[res.name] = "success" if res.success else "failed"
                # 把子 agent 实际查询到的内容推给前端作为思考块，让用户看到"模型真查到了什么"
                if res.content:
                    self._emit_worker_result(writer, res.name, res.content)
            for w in runnable:
                st = status.get(w)
                if st == "success":
                    self._emit(writer, f"✅ {w} 完成")
                elif st == "skipped":
                    self._emit(writer, f"⏭️ {w} 本轮无需技能，跳过")
                else:
                    self._emit(writer, f"⚠️ {w} 结果异常")
        else:
            self._emit(writer, "📋 本轮无可执行数据子 agent，直接综合")

        update: dict = {"worker_outputs": outputs, "worker_status": status, "active_workers": active}
        # 推理子 agent 独立记忆深合并（keep_history agent 的 history_update 才存在）
        for hu in history_updates:
            for key, val in hu.items():
                merged = dict(update.get(key) or {})
                if isinstance(val, dict):
                    merged.update(val)
                update[key] = merged
        return update

    async def node_synthesize(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·综合：基于 L1 子 agent 结果生成合规草稿（未经安全门卫的中间产物）"""
        plan = SupervisorPlan.model_validate(state.get("agent_plan") or {})
        if plan.needs_workers is False:
            # 兜底：理论上已被 direct_reply_node 分流；此处兼容直接调用
            draft = await self.supervisor.synthesize(state, direct_reply=plan.direct_reply)
        else:
            draft = await self.supervisor.synthesize(
                state, worker_outputs=state.get("worker_outputs") or {}
            )
        return {"final_answer_draft": draft}

    async def node_safety(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·L3 安全门卫：如需用药复核则 safety_agent 审核 + 免责声明追加"""
        plan = SupervisorPlan.model_validate(state.get("agent_plan") or {})
        draft = state.get("final_answer_draft") or ""
        writer = self._stream()
        blocked = False
        violations: list = []
        # 合并前序节点的子 agent 审计字段，避免覆盖 L1 结果
        ws = dict(state.get("worker_status") or {})
        wo = dict(state.get("worker_outputs") or {})

        if plan.needs_medication_safety and self.registry.get("safety_agent"):
            sres = await self._run_agent("safety_agent", state, human_input=draft, payload={})
            if sres is not None:
                ws["safety_agent"] = "success" if sres.success else "failed"
                wo["safety_agent"] = sres.content
                # 结构化判定（SafetyVerdict → content 以"违规"开头）+ regex 兜底双保险
                if sres.success and sres.content.startswith("违规"):
                    blocked = True
                    raw = sres.content[2:].lstrip("：;；，")
                    violations = [v.strip() for v in raw.split("；") if v.strip()] if raw else []
                elif _PRESCRIPTION_RE.search(draft):
                    blocked = True
                    violations = _PRESCRIPTION_RE.findall(draft)
                if blocked:
                    if "仅供参考" in draft:
                        # draft 已由 supervisor 自带免责声明，幂等跳过，避免重复叠加
                        self._emit(writer, "🛡️ safety_agent 复核通过（回答已含'仅供参考'免责声明）")
                    else:
                        self._emit(writer, "🛡️ safety_agent 提示：检测到用药/剂量内容，已追加'仅供参考'免责声明")
                        draft = draft.rstrip() + "\n\n" + DISCLAIMER_MEDICAL_ADVICE
                else:
                    self._emit(writer, "🛡️ safety_agent 复核通过")

        verdict = {
            "blocked": blocked,
            "violations": violations,
            "worker": "safety_agent" if self.registry.get("safety_agent") else None,
        }
        # 只输出已过门卫的最终文本（与旧编排一致：审批通过后才 stream_output）
        self.supervisor.stream_output(draft)
        return {"final_answer": draft, "safety_verdict": verdict, "worker_status": ws, "worker_outputs": wo}

    async def node_direct_reply(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·纯闲聊直达综合（needs_workers=False）：先给 general_agent 一次
        低成本技能机会（关键词/FTS5 试探，命中才执行），未命中才直接寒暄。"""
        update: dict = {}
        gr = await self._probe_general_agent(state)
        if gr is not None and (gr.success or gr.content):
            wo = {_GENERAL_AGENT: gr.content}
            update["active_workers"] = [_GENERAL_AGENT]
            update["worker_outputs"] = wo
            update["worker_status"] = {_GENERAL_AGENT: "success" if gr.success else "failed"}
            reply = await self.supervisor.synthesize(state, worker_outputs=wo)
        else:
            plan = SupervisorPlan.model_validate(state.get("agent_plan") or {})
            update["active_workers"] = []
            update["worker_outputs"] = {}
            update["worker_status"] = {}
            reply = await self.supervisor.synthesize(state, direct_reply=plan.direct_reply)
        self.supervisor.stream_output(reply)
        update["final_answer"] = reply
        return update

    async def node_image_fast(self, state: DigitalSmartDoctorState) -> dict:
        """子图节点·图片快路径（免 plan，直接成像 agent + 综合）"""
        img_skill = self._extract_img_skill(state)
        return await self._image_fast_path(state, img_skill)