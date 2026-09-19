"""后处理节点
格式化输出
清理中间状态
准备用户界面数据
"""

from langchain_core.messages import AIMessage, HumanMessage

from agent.core.state import DigitalSmartDoctorState


class PostProcessNode:
    """后处理节点：保存历史记录，清理状态"""
    
    @staticmethod
    async def execute(state: DigitalSmartDoctorState) -> dict:
        """执行后处理逻辑
        
        Args:
            state: 当前状态
            
        Returns:
            更新后的状态字典
        """
        history_message = state["messages"]

        history_message.append(HumanMessage(content=state["human_input"]))
        history_message.append(AIMessage(content=str(state["final_answer"])))

        # 保存会话记录，初始化状态
        return {
            "messages": history_message,
            "human_input": "",
            "final_answer": "",
            "sub_agent_input": "",
            # 多 Agent：本轮编排临时字段一并清理（worker_messages 保留，跨轮记忆由 checkpoint 持久化）
            "worker_outputs": None,
            "agent_plan": None,
            "final_answer_draft": None,
            "safety_verdict": None,
        }
