"""路由节点
决策逻辑:判断「对话是否继续 / 是否结束」，中断暂停 节点
"""

from agent.core.state import DigitalSmartDoctorState


class RoutingNode:
    """路由节点：空节点和对话继续判断"""
    
    @staticmethod
    async def empty_node(state: DigitalSmartDoctorState) -> dict:
        """空节点，用于中断点
        
        Args:
            state: 当前状态
            
        Returns:
            空字典
        """
        return {}
    
    @staticmethod
    async def chat_continue(state: DigitalSmartDoctorState) -> str:
        """判断对话是否继续 —— 恒返回 "pre_process"，本图不设"结束"出口。

        设计说明（勿改成返回 END）：
        - 每一轮的停止由 compile(interrupt_after=["node_empty"]) 负责：图跑到 node_empty
          就被挂起、状态写入 checkpoint、控制权交回用户。本路由只在**下一轮唤醒**时决定去哪。
        - 之所以恒回 pre_process：多轮记忆存在 messages 里，而 messages 在 state.py:11
          是普通字段（无 add_messages 合并 reducer）。若这里返回 END，checkpoint 的 next
          会变空，下一轮 digital_smart_doctor_agent.aprocess 会走"全新开始"分支，
          init_state 的 messages=[] 会直接覆盖掉全部对话历史 —— 每轮都像跟失忆的医生说话。

        Args:
            state: 当前状态

        Returns:
            固定为 "pre_process"（回到图起点，开始下一轮）
        """
        return "pre_process"
