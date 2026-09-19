"""多智能体子图构建器 - 把 multi-agent 编排在 LangGraph 图上可见（P1 升级）

把 MultiAgentOrchestrator 拆出的节点方法（plan → agents 并行 → synthesize → safety）
编译成一张独立子图，作为主图 node_multi_agent 接入，从而：
  - supervisor 规划 / L1 子 agent 并行 / 综合 / 安全门卫 在图上成为独立、可单点调试的节点；
  - 主图节点名、checkpoint、interrupt 逻辑保持不变（保护既有 MySQL checkpoint 的多轮续跑）；
  - 子 agent 并行暂用 asyncio.gather（LangGraph 运行时仍是单节点并发），未来可升级 Send API。
"""

from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph

from agent.core.state import DigitalSmartDoctorState


def build_multi_agent_graph(orchestrator) -> CompiledStateGraph:
    """把 orchestrator 的编排流程编译为多智能体子图

    Args:
        orchestrator: MultiAgentOrchestrator（已注入 registry / supervisor / dispatcher）

    Returns:
        编译后的子图（无独立 checkpointer，共享主图 checkpoint）

    子图拓扑（与 orchestrator 旧单节点编排的行为一一对应）：
        START → plan_node ──(条件)──→ image_fast_node → END
                              ├──────→ direct_reply_node → END
                              └──────→ workers_parallel_node → synthesize_node → safety_node → END
    """
    builder = StateGraph(DigitalSmartDoctorState)

    builder.add_node("plan_node", orchestrator.node_plan)
    builder.add_node("image_fast_node", orchestrator.node_image_fast)
    builder.add_node("direct_reply_node", orchestrator.node_direct_reply)
    builder.add_node("workers_parallel_node", orchestrator.node_workers)
    builder.add_node("synthesize_node", orchestrator.node_synthesize)
    builder.add_node("safety_node", orchestrator.node_safety)

    builder.add_edge(START, "plan_node")

    # 条件路由：图片快路径 / 纯闲聊直达综合 / 正常 worker 并行
    builder.add_conditional_edges(
        "plan_node",
        orchestrator.route_after_plan,
        {
            "image_fast_node": "image_fast_node",
            "direct_reply_node": "direct_reply_node",
            "workers_parallel_node": "workers_parallel_node",
        },
    )

    builder.add_edge("image_fast_node", END)
    builder.add_edge("direct_reply_node", END)

    # 正常路径：并行取数 → 合规综合 → 安全门卫 → 输出
    builder.add_edge("workers_parallel_node", "synthesize_node")
    builder.add_edge("synthesize_node", "safety_node")
    builder.add_edge("safety_node", END)

    return builder.compile(checkpointer=None)