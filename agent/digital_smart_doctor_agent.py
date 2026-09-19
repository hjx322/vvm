"""数字智能医生 Agent - 主入口"""

from typing import Any, Iterator, Optional

import aiomysql
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
from loguru import logger

from config.app_config import configs
from agent.core.constants import configure_logging
from agent.core.graph_builder import GraphBuilder
from agent.core.state import DigitalSmartDoctorState
from agent.nodes import (
    PostProcessNode,
    PreProcessNode,
    RoutingNode,
    ImageProcessNode,
)
from agent.utils.llm_service import LLMService
from agent.utils import handle_restart
from agent.utils.emergency import detect_emergency, emergency_reply

# 配置日志
configure_logging()


async def _emergency_stream(text: str):
    """把急症指引包装成与 aprocess(stream) 一致的 custom 事件流

    yield 的 dict 形状与 orchestrator / supervisor 推给前端的一致（type=content），
    因此 chat_server 的 _sse_stream 无需任何改动即可透传。
    """
    yield {"type": "content", "content": text}


class DigitalSmartDoctorAgent:
    """数字智能医生 Agent - 主入口类（异步版本）"""

    def __init__(self, workflow_id: str):
        """初始化 Agent（不创建连接，等待 create() 方法）

        Args:
            workflow_id: 工作流 ID
        """
        self.workflow_id = workflow_id
        self.config = RunnableConfig(configurable={"thread_id": self.workflow_id})
        self.connection = None
        self.checkpoint = None
        self.llm = None
        self.graph = None

    @classmethod
    async def create(cls, workflow_id: str):
        """异步工厂方法创建 Agent 实例

        Args:
            workflow_id: 工作流 ID

        Returns:
            初始化完成的 Agent 实例

        Raises:
            Exception: 如果初始化过程中发生错误
        """
        instance = cls(workflow_id)

        try:
            # 创建异步数据库连接
            instance.connection = await aiomysql.connect(
                host=configs.db.mysql.host,
                port=configs.db.mysql.port,
                user=configs.db.mysql.username,
                password=configs.db.mysql.password,
                db=configs.db.mysql.db,
                autocommit=True,
            )
            logger.info(f"MySQL 连接成功建立: {configs.db.mysql.host}:{configs.db.mysql.port}")

            # 初始化异步 checkpointer
            instance.checkpoint = AIOMySQLSaver(conn=instance.connection)
            # 注意：setup() 只需要在首次使用时调用一次来创建表
            # 如果表已存在（例如之前使用过 PyMySQLSaver），则无需再次调用
            # await instance.checkpoint.setup()

            # 初始化 LLM
            instance.llm = LLMService.create_llm()

            # 构建 Graph
            instance.graph = instance._build_graph()

            return instance
        except Exception as e:
            # 如果初始化失败，确保清理已创建的资源
            await instance.close()
            logger.error(f"Agent 初始化失败: {e}")
            raise

    async def close(self):
        """关闭数据库连接，释放资源"""
        if self.connection:
            try:
                self.connection.close()
            except Exception as e:
                logger.warning(f"关闭数据库连接时出错: {e}")
            finally:
                self.connection = None
                self.checkpoint = None
    
    async def __aenter__(self):
        """异步上下文管理器入口
        
        Returns:
            Agent 实例自身
        """
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出，自动清理资源
        
        Args:
            exc_type: 异常类型
            exc_val: 异常值
            exc_tb: 异常追踪信息
        """
        await self.close()

    def _build_graph(self):
        """构建状态图

        恒走多 Agent 编排（node_multi_agent = 编译后的 supervisor-agent 子图）：
        supervisor 决定本轮用哪些子 agent，子 agent 内部用三级技能调度选择技能并执行。
        主图节点名（pre_process/image_process_node/node_multi_agent/node_suf_process/node_empty）
        保持不变，保护既有 MySQL checkpoint 的多轮续跑。

        Returns:
            编译后的状态图
        """
        from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher

        pre_process_node = PreProcessNode()
        image_process_node = ImageProcessNode()  # 独立初始化，无需 llm
        post_process_node = PostProcessNode()
        routing_node = RoutingNode()

        # ---- 多 Agent 路径（恒开启）：node_multi_agent = 主管-工人编排 ----
        from agent.multi_agent.agent_registry import AgentRegistry
        from agent.multi_agent.orchestrator import MultiAgentOrchestrator
        from agent.multi_agent.supervisor import Supervisor
        from agent.multi_agent.workers import build_all_agents

        dispatcher = UnifiedSkillDispatcher(self.llm)
        # 子 agent 技能配置提供器（从 DB 读各子 agent 启用的技能，替代硬编码）
        from agent.multi_agent.worker_skill_provider import AgentSkillProvider

        skill_provider = AgentSkillProvider()
        registry = AgentRegistry(dispatcher=dispatcher, skill_provider=skill_provider)
        # 各子 agent 不传 llm → 按自身 role 创建独立分级模型实例（create_agent_llm）
        for agent in build_all_agents(dispatcher=dispatcher):
            registry.register(agent)
        supervisor = Supervisor()  # qwen-max（结构化规划更稳）
        orchestrator = MultiAgentOrchestrator(registry, supervisor, dispatcher)
        # 把编排编译为多智能体子图（plan→workers→synthesize→safety 在图上可见），
        # 主图节点名 node_multi_agent 保持不变，MySQL checkpoint 多轮续跑兼容
        from agent.multi_agent.graph import build_multi_agent_graph

        multi_agent_graph = build_multi_agent_graph(orchestrator)

        graph_builder = GraphBuilder(
            pre_process_node=pre_process_node,
            post_process_node=post_process_node,
            routing_node=routing_node,
            image_process_node=image_process_node,
            checkpointer=self.checkpoint,
            multi_agent_graph=multi_agent_graph,
        )

        return graph_builder.build()

    async def aprocess(
        self,
        human_input: str,
        crm: str,
        chat_name: Optional[str] = None,
        doctor_id: Optional[str] = None,
        response_type: str = "stream",
        restart: bool = False,
        image_path: Optional[str] = None,
        medical_record_no: Optional[str] = None,
    ):
        """处理用户输入（异步版本）

        Args:
            human_input: 用户输入
            crm: CRM数据库名
            chat_name: 会话名
            doctor_id: 医生智能体id
            response_type: 响应类型，"stream" 或 "normal"
            restart: 是否重启会话
            image_path: 图片路径
            medical_record_no: 显式病历号（前端下拉选定患者时透传完整编号，绕过正则提取）

        Returns:
            异步生成器（stream模式）或字符串（normal模式）

        急症早返回：命中急症硬规则时直接返回急诊指引，**不进入 LangGraph 图**，
        因此不产生任何 LLM 调用、也不写 checkpoint（详见 agent/utils/emergency.py）。
        """
        # ===== 急症前置硬规则（Fix: 原实现把急症交给 LLM 流水线，47.9s 后才提示就医）=====
        if getattr(configs.emergency, "enabled", True):
            emergency_category = detect_emergency(human_input)
            if emergency_category:
                logger.warning(f"[Emergency] 命中急症规则: {emergency_category}")
                reply = emergency_reply(emergency_category)
                if response_type == "stream":
                    return _emergency_stream(reply)
                return reply

        init_state = self._init_state(
            human_input,
            chat_name,
            crm,
            doctor_id,
            image_path=image_path,
            medical_record_no=medical_record_no,
        )

        # ===== 每轮都做"异常中断自愈"检查 =====
        # handle_restart 只在 saved_state.next 指向非收尾节点（既不是 pre_process 也不是
        # node_empty）时才重置，正常停在 node_empty 时是空操作，故每轮调用幂等。
        #
        # 为什么不能只在 restart=True 时调（原先的写法）：
        #   ① 用户中止流式生成时，chat_server 会 aclose 掉图 → checkpoint 停在中间节点；
        #   ② 进程中途被杀 / 某节点抛异常 → 同样停在中间节点。
        # 若不自愈，下一轮会带着**上一轮的 agent_plan** 从中间节点续跑，产出
        # "旧计划 + 新问题"的错误回答。handle_restart 会保留 messages（多轮记忆不丢）
        # 与患者字段，仅把图重置回 node_empty。
        # restart 参数保留兼容（前端首轮仍会传 true），语义已被本调用覆盖。
        await handle_restart(self.graph, self.config, init_state)

        saved_state = await self.graph.aget_state(self.config)
        if not saved_state or not saved_state.next:
            result = self.graph.astream(
                init_state,
                config=self.config,
                stream_mode="custom",
            )
        else:
            update_data = {"human_input": human_input}
            if medical_record_no:
                update_data["medical_record_no"] = medical_record_no
            if getattr(saved_state.values, "medical_record_no", None):
                update_data["chat_name"] = chat_name or ""
            # 前端新上传图片时，将图片路径注入续跑状态并重置图片处理进度
            if image_path:
                update_data["image_path"] = image_path
                update_data["image_description"] = None
                update_data["image_processing_status"] = "idle"
                update_data["image_available_skills"] = []
            await self.graph.aupdate_state(self.config, update_data)
            result = self.graph.astream(None, config=self.config, stream_mode="custom")
        
        if response_type == "stream":
            return result
        if response_type == "normal":
            # 收集所有流式 chunk，拼接成完整文本返回
            full_text = ""
            async for chunk in result:
                content = (
                    chunk.get("content", "")
                    if isinstance(chunk, dict)
                    else getattr(chunk, "content", "")
                )
                full_text += content
            return full_text
        return ""

    @staticmethod
    def _init_state(
        human_input: str,
        chat_name: Optional[str],
        crm: str,
        doctor_id: Optional[str],
        image_path: Optional[str] = None,
        medical_record_no: Optional[str] = None,
    ) -> DigitalSmartDoctorState:
        """初始化状态

        Args:
            human_input: 用户输入
            chat_name: 会话名称
            crm: CRM 数据库名
            doctor_id: 医生智能体id
            image_path: 图片路径（可选）
            medical_record_no: 显式病历号（可选，优先于 chat_name 正则提取）

        Returns:
            初始化后的状态字典
        """
        return {
            "human_input": human_input,
            "messages": [],
            "chat_name": chat_name,
            "doctor_id": doctor_id,
            "user_id": None,  # 由 pre_process 从 chat_name 提取
            "crm": crm,
            "medical_record_no": medical_record_no,
            "patient_info": None,
            "patient_visit_record": None,
            "patient_examine_result": None,
            "context_info": None,
            "final_answer": None,
            "sub_agent_input": "",
            # 多 Agent 编排初始字段
            "worker_outputs": {},
            "worker_messages": {},  # 推理 worker 跨轮记忆（续跑时从 checkpoint 读回）
            "agent_plan": None,
            "final_answer_draft": None,
            "safety_verdict": None,
            "active_workers": [],
            "worker_status": {},
            # 图片处理相关字段
            "image_path": image_path,
            "image_description": None,
            "image_processing_status": "idle",
            "image_available_skills": [],
            "image_pending_skill": None,  # 待用户确认的图片技能（低置信度反问时暂存）
        }
