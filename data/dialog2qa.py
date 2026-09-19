import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from tqdm import tqdm

from config.app_config import configs
from data.datasets.dialog_dataset import DialogDataloader
from prompt.dialog2qa_prompt import DIALOG_2_QA_SYSTEM_PROMPT

if __name__ == "__main__":
    dashscope_api_key = configs.llm.dashscope.api_key
    loader = DialogDataloader(filepath="data/dialogs")
    f = open("qa.json", "w", encoding="utf-8")

    for batch in tqdm(loader, "正在使用大模型进行QA对提取"):
        one_batch_content = []
        for c in batch:
            messages = [
                SystemMessage(content=DIALOG_2_QA_SYSTEM_PROMPT),
                HumanMessage(content=c),
            ]
            llm = ChatOpenAI(
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key=SecretStr(str(dashscope_api_key)),
                temperature=0.2,
                model="qwen-plus",
            )
            _reply = ""
            for chunk in llm.stream(messages):
                content = chunk.content if hasattr(chunk, "content") else ""
                _reply += content
            full_one_turn_content = {"source_data": c, "data": _reply}
            one_batch_content.append(full_one_turn_content)
        json.dump(one_batch_content, f, ensure_ascii=False, indent=2)
    f.close()
