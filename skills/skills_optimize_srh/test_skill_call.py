# -*- coding: utf-8 -*-
"""测试技能调用的脚本"""
import asyncio
import uuid
from agent.digital_smart_doctor_agent import DigitalSmartDoctorAgent

async def test_skill_call():
    """测试技能调用"""
    workflow_id = str(uuid.uuid4())
    chat_name = "姓名 1827196"  # 对应医疗档案号 1827196
    crm = "hn"
    doctor_id = "agt_d75e25a434fa457f"

    # 创建 Agent
    print("正在初始化 Agent...")
    agent = await DigitalSmartDoctorAgent.create(workflow_id=workflow_id)

    try:
        # 测试输入：需要调用技能的问题
        test_inputs = [
            "帮我制定一个健身训练计划",  # 调用 healthfit 技能
        ]

        for human_input in test_inputs:
            print(f"\n{'='*60}")
            print(f"用户问题: {human_input}")
            print(f"{'='*60}")

            response = await agent.aprocess(
                human_input, crm, chat_name, doctor_id,
                response_type="stream", restart=False
            )

            print("AI 回复: ", end="")
            async for chunk in response:
                content = chunk.get("content", "") if isinstance(chunk, dict) else getattr(chunk, "content", "")
                print(content, end="", flush=True)
            print("\n")

    finally:
        await agent.close()
        print("\n测试完成")

if __name__ == '__main__':
    asyncio.run(test_skill_call())
