import asyncio
import uuid
from agent.digital_smart_doctor_agent import DigitalSmartDoctorAgent

async def main():
    """异步主函数"""
    workflow_id = str(uuid.uuid4())
    #workflow_id = "digital_smart_doctor_agent_xbt"
    chat_name = "姓名 1827196"
    response_type = "stream"
    crm = "hn"
    doctor_id = "agt_d75e25a434fa457f"
    
    # 使用异步工厂方法创建 Agent
    agent = await DigitalSmartDoctorAgent.create(workflow_id=workflow_id)
    
    print("对话已启动，输入 'exit' 退出。")
    first_round = True
    
    try:
        while True:
            # HUMAN 输入
            human_input = input("HUMAN: ")
            # 退出条件
            if human_input.strip().lower() == "exit":
                print("对话结束")
                break
            
            # AI 回复
            print("AI: ", end="")
            # 使用异步 aprocess
            ai_reply = await agent.aprocess(
                human_input, crm, chat_name, doctor_id , response_type, restart=first_round
            )   
            first_round = False
            
            if response_type == "normal":
                print(ai_reply)
            if response_type == "stream":
                async for chunk in ai_reply:
                    print(chunk["content"], end="", flush=True)
                print("\n")
    finally:
        # 关闭数据库连接（异步）
        if agent.connection:
            await agent.connection.ensure_closed()

if __name__ == '__main__':
    asyncio.run(main())
