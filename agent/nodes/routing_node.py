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
        """判断对话是否继续
        
        Args:
            state: 当前状态
            
        Returns:
            下一个节点名称
        """
        return "pre_process"
