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
    ChatNode,
    PostProcessNode,
    PreProcessNode,
    RoutingNode,
    SkillQueryNode,
    ImageProcessNode,
)
from agent.utils.llm_service import LLMService
from agent.utils import handle_restart

# 配置日志
configure_logging()


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

        Returns:
            编译后的状态图
        """
        # 初始化所有节点
        pre_process_node = PreProcessNode()
        image_process_node = ImageProcessNode()  # 独立初始化，无需 llm
        # 新架构：用 UnifiedSkillDispatcher 替代 SkillQueryNode
        from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher
        skill_query_node = UnifiedSkillDispatcher(self.llm)
        chat_node = ChatNode(self.llm)
        post_process_node = PostProcessNode()
        routing_node = RoutingNode()

        # 使用 GraphBuilder 构建图
        graph_builder = GraphBuilder(
            pre_process_node=pre_process_node,
            skill_query_node=skill_query_node,
            chat_node=chat_node,
            post_process_node=post_process_node,
            routing_node=routing_node,
            image_process_node=image_process_node,
            checkpointer=self.checkpoint,
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
        """
        init_state = self._init_state(
            human_input,
            chat_name,
            crm,
            doctor_id,
            image_path=image_path,
            medical_record_no=medical_record_no,
        )

        if restart:
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
            # 图片处理相关字段
            "image_path": image_path,
            "image_description": None,
            "image_processing_status": "idle",
            "image_available_skills": [],
        }
