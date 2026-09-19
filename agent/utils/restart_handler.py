"""会话重启处理逻辑"""

from loguru import logger
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from agent.core.state import DigitalSmartDoctorState


async def handle_restart(
    graph: CompiledStateGraph,
    config: RunnableConfig,
    init_state: DigitalSmartDoctorState
) -> None:
    """处理重启逻辑：检查当前状态，如果处于异常中断状态，则执行状态重置。

    Args:
        graph: 编译后的状态图
        config: 运行配置
        init_state: 初始状态对象，用于在重置时继承旧记忆

    Returns:
        None
    """
    # 尝试获取旧状态（使用异步方法）
    old_state = await graph.aget_state(config)
    
    # 判断是否需要归档重启
    # 只有当处于非正常中断状态（例如卡在中间节点）时才重启
    # 正常中断状态是：图执行到了 node_empty 并且 interrupt_after 生效
    # 此时 saved_state.next 应该是空的或者指向下一个条件分支
    # 只在"非 pre_process / node_empty" 的情况下重启
    
    should_restart = False
    if old_state and old_state.next:
        # 获取即将执行的节点列表
        next_nodes = old_state.next
        is_normal_pause = False
        for node in next_nodes:
            # 如果下一个是 pre_process，说明上次正常结束，这次准备开始新一轮 -> 正常
            if node == "pre_process":
                is_normal_pause = True
                break
            # 如果是 node_empty，通常意味着刚执行完它，或者准备执行它（取决于 interrupt 是 before 还是 after）
            # 这里 interrupt_after=_interrupt_node (["node_empty"])
            # 意味着执行完 node_empty 后暂停。
            # 此时 next 应该指向 conditional edge 的结果，即 pre_process 或 end
            if node == "node_empty":
                is_normal_pause = True
                break
        
        if not is_normal_pause:
            should_restart = True

    if should_restart:
        # 准备继承的旧状态数据
        if old_state and old_state.values:
            if "messages" in old_state.values:
                init_state["messages"] = old_state.values["messages"]
            for key in ["medical_record_no", "patient_info", "context_info"]:
                if key in old_state.values and old_state.values[key]:
                    init_state[key] = old_state.values[key]
        
        # as_node="pre_process" 告诉 LangGraph 我们假装刚刚执行完了 pre_process
        # 这样它就会从 pre_process 的后继节点继续执行，实现重置效果
        logger.info(f"检测到会话异常中断，正在通过 update_state 重置到初始节点...")
        await graph.aupdate_state(
            config,
            init_state,
            as_node="node_empty"
        )
        logger.info("会话已重置，保持当前 thread_id 不变。")
