"""对话节点
调用LLM(QWEN Plus)
整合技能结果
生成最终回答
"""

from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate
from langgraph.config import get_stream_writer

from agent.core.state import DigitalSmartDoctorState
from prompt.chat_prompt import PROMPT_CHAT_DERMATOLOGIST


class ChatNode:
    """对话节点：生成 AI 回复"""
    
    def __init__(self, llm):
        """初始化对话节点
        
        Args:
            llm: LLM 实例
        """
        self.llm = llm
    
    async def execute(self, state: DigitalSmartDoctorState) -> dict:
        """执行对话逻辑（异步版本）
        
        Args:
            state: 当前状态
            
        Returns:
            包含最终回答的状态字典
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        chat_history = state['messages']
        sub_agent_input = state["sub_agent_input"]
        patient_outpatient_record = ""  # TODO: 门诊记录没有实现
        context_info = (
            state["context_info"]
            if "context_info" in state and state["context_info"]
            else ""
        )
        
        _sys_prompt = PromptTemplate(
            template=PROMPT_CHAT_DERMATOLOGIST,
            input_variables=[
                "current_time",
                "chat_history",
                "patient_outpatient_record",
                "sub_agent_input",
                "context_info",
            ],
        ).format(
            current_time=current_time,
            chat_history=chat_history,
            patient_outpatient_record=patient_outpatient_record,
            sub_agent_input=sub_agent_input,
            context_info=context_info,
        )
 
        messages = [
            SystemMessage(content=_sys_prompt),
            HumanMessage(content=state["human_input"]),
        ]

        # 清理 surrogate 字符，防止 UTF-8 编码错误
        for msg in messages:
            if isinstance(msg.content, str):
                msg.content = msg.content.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")

        stream_writer = get_stream_writer()  # 这里只是获取对外输出的接口
        _reply = ""
        async for chunk in self.llm.astream(messages):
            content = chunk.content if hasattr(chunk, "content") else ""
            _reply += str(content)
            stream_writer({"content": content})

        return {"final_answer": _reply}
