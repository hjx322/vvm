"""Graph 构建器 - 封装 LangGraph 的构建逻辑（恒走多 Agent 子图，无 legacy 分支）"""

from langgraph.constants import START
from langgraph.graph.state import CompiledStateGraph, StateGraph

from agent.core.constants import INTERRUPT_NODES
from agent.core.state import DigitalSmartDoctorState


class GraphBuilder:
    """Graph 构建器：负责构建和编译 StateGraph"""

    def __init__(
        self,
        pre_process_node,
        post_process_node,
        routing_node,
        image_process_node,
        checkpointer,
        multi_agent_graph,  # 多 Agent 已编译子图：作为主图 node_multi_agent 接入
    ):
        """初始化 Graph Builder

        Args:
            pre_process_node: 预处理节点实例
            post_process_node: 后处理节点实例
            routing_node: 路由节点实例
            image_process_node: 图片处理节点实例（已独立初始化）
            checkpointer: Checkpoint 保存器
            multi_agent_graph: 多 Agent 已编译子图（supervisor/workers/synthesize/safety
                               在图上可见，作为主图 node_multi_agent 节点接入）
        """
        self.pre_process_node = pre_process_node
        self.post_process_node = post_process_node
        self.routing_node = routing_node
        self.image_process_node = image_process_node
        self.checkpointer = checkpointer
        self.multi_agent_graph = multi_agent_graph
        # 主链路编排节点名：多 Agent 恒开启 → node_multi_agent（supervisor-worker 子图）
        self.skill_target = "node_multi_agent"

    def _route_after_preprocess(self, state: DigitalSmartDoctorState) -> str:
        """条件分支：根据是否有图片决定走哪条路

        Args:
            state: 当前状态

        Returns:
            节点名称：image_process_node 或 self.skill_target
        """
        if state.get("image_path"):
            return "image_process_node"
        else:
            return self.skill_target

    def build(self) -> CompiledStateGraph:
        """构建并编译 StateGraph

        Returns:
            编译后的状态图
        """
        builder = StateGraph(DigitalSmartDoctorState)  # 创建一个 LangGraph 的状态图

        builder.add_node("pre_process", self.pre_process_node.execute)
        builder.add_node("image_process_node", self.image_process_node.execute)
        # node_multi_agent = 编译后的多智能体子图（supervisor/workers/synthesize/safety
        # 在图上可见、可单点调试），主图 checkpoint 由本层 checkpointer 统一管理
        builder.add_node("node_multi_agent", self.multi_agent_graph)
        builder.add_node("node_suf_process", self.post_process_node.execute)
        builder.add_node("node_empty", self.routing_node.empty_node)  # TODO:在这个点停止

        builder.add_edge(START, "pre_process")

        builder.add_conditional_edges(
            "pre_process",
            self._route_after_preprocess,
            {
                "image_process_node": "image_process_node",
                self.skill_target: self.skill_target,
            }
        )

        # 图片处理节点后：根据处理状态决定下一步
        # 如果 image_processing_status 是 waiting_for_user_intent，就停在 node_empty
        # 如果 image_processing_status 是 skill_executed，继续到 node_multi_agent
        builder.add_conditional_edges(
            "image_process_node",
            self._route_after_image_process,
            {
                self.skill_target: self.skill_target,
                "node_empty": "node_empty",
            }
        )

        builder.add_edge("node_multi_agent", "node_suf_process")
        builder.add_edge("node_suf_process", "node_empty")

        # node_empty 之后固定回到 pre_process —— 本图是一个"多轮回路"，每一轮的
        # 停止由 compile(interrupt_after=INTERRUPT_NODES) 负责，**刻意不设通往 END 的出口**。
        # 为什么不能接 END：一旦走到 END，checkpoint 的 next 会变成空元组，下一轮
        # aprocess 会走"全新开始"分支，而 init_state 里 messages=[] 会直接覆盖掉多轮
        # 对话历史（messages 无合并 reducer，见 state.py:11）→ 每轮都像失忆。
        builder.add_conditional_edges(
            "node_empty",
            self.routing_node.chat_continue,
            {
                "pre_process": "pre_process",
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

        if image_processing_status == "waiting_for_user_intent":
            # 等待用户回答，回到 node_empty 停止
            return "node_empty"
        elif image_processing_status == "waiting_for_skill_confirmation":
            # 等待用户确认，回到 node_empty 停止
            return "node_empty"
        else:
            # 图片技能已执行 或 无需等待 → 进入 node_multi_agent（多智能体编排）
            return self.skill_target