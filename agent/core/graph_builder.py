"""Graph 构建器 - 封装 LangGraph 的构建逻辑"""

from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph

from agent.core.constants import INTERRUPT_NODES
from agent.core.state import DigitalSmartDoctorState


class GraphBuilder: 
    """Graph 构建器：负责构建和编译 StateGraph"""

    def __init__(
        self,
        pre_process_node,
        skill_query_node,
        chat_node,
        post_process_node,
        routing_node,
        image_process_node,
        checkpointer
    ):
        
        """初始化 Graph Builder

        Args:
            pre_process_node: 预处理节点实例
            skill_query_node: Skill 查询节点实例
            chat_node: 对话节点实例
            post_process_node: 后处理节点实例
            routing_node: 路由节点实例
            image_process_node: 图片处理节点实例（已独立初始化）
            checkpointer: Checkpoint 保存器
        """
        self.pre_process_node = pre_process_node
        self.skill_query_node = skill_query_node
        self.chat_node = chat_node
        self.post_process_node = post_process_node
        self.routing_node = routing_node
        self.image_process_node = image_process_node
        self.checkpointer = checkpointer

    def _route_after_preprocess(self, state: DigitalSmartDoctorState) -> str:
        """条件分支：根据是否有图片决定走哪条路

        Args:
            state: 当前状态

        Returns:
            节点名称：image_process_node 或 node_query_skills
        """
        if state.get("image_path"):
            return "image_process_node"
        else:
            return "node_query_skills"

    def build(self) -> CompiledStateGraph: 
        """构建并编译 StateGraph

        Returns:
            编译后的状态图
        """
        builder = StateGraph(DigitalSmartDoctorState) #创建一个 LangGraph 的状态图

        builder.add_node("pre_process", self.pre_process_node.execute)
        builder.add_node("image_process_node", self.image_process_node.execute)
        builder.add_node("node_query_skills", self.skill_query_node.dispatch)
        builder.add_node("node_chat", self.chat_node.execute)
        builder.add_node("node_suf_process", self.post_process_node.execute)
        builder.add_node("node_empty", self.routing_node.empty_node)  # TODO:在这个点停止

        builder.add_edge(START, "pre_process")

        builder.add_conditional_edges(
            "pre_process",
            self._route_after_preprocess,
            {
                "image_process_node": "image_process_node",
                "node_query_skills": "node_query_skills",
            }
        )

        # 图片处理节点后：根据处理状态决定下一步
        # 如果 image_processing_status 是 waiting_for_user_intent，就停在 node_empty
        # 如果 image_processing_status 是 skill_executed，继续到 node_query_skills
        builder.add_conditional_edges(
            "image_process_node",
            self._route_after_image_process,
            {
                "node_query_skills": "node_query_skills",
                "node_empty": "node_empty",
            }
        )

        builder.add_edge("node_query_skills", "node_chat")
        builder.add_edge("node_chat", "node_suf_process")
        builder.add_edge("node_suf_process", "node_empty")

        builder.add_conditional_edges(
            "node_empty",
            self.routing_node.chat_continue,
            {
                "pre_process": "pre_process",
                "end": END,
            },
        )

        graph = builder.compile(
            checkpointer=self.checkpointer,
            interrupt_after=INTERRUPT_NODES
        )  

        return graph

    def _route_after_image_process(self, state: DigitalSmartDoctorState) -> str:
        """图片处理后的路由决策

        Args:
            state: 当前状态

        Returns:
            下一个节点的名称
        """
        image_processing_status = state.get("image_processing_status", "idle")

        if image_processing_status == "skill_executed":
            # 技能已执行，继续到 node_query_skills（已通过 sub_agent_input 传递参数）
            return "node_query_skills"
        elif image_processing_status == "waiting_for_user_intent":
            # 等待用户回答，回到 node_empty 停止
            return "node_empty"
        elif image_processing_status == "waiting_for_skill_confirmation":
            # 等待用户确认，回到 node_empty 停止
            return "node_empty"
        elif image_processing_status == "idle":
            # 处理完成，进入正常流程
            return "node_query_skills"
        else:
            # 其他状态，进行正常流程
            return "node_query_skills"

