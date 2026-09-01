###   ===========================================================================
###                                      文件说明
###   ===========================================================================
# __file__ = "qa_vector.py"
# Description:处理QA的时候分成55个batch，所以len(data)为55,然后这55个块内的每一条信息是一次对话的原信息及其QA
import json
import os
from pathlib import Path

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from tqdm import tqdm

from config.app_config import configs
from vector.milvus_vector import MilvusVector

if __name__ == "__main__":
    qa_file_path = "qa.json"
    dashscope_api_key=configs.llm.dashscope.api_key
    f = open(qa_file_path, "r", encoding="utf-8")
    data = json.load(f)
    filepath="data/dialogs"
    root_dir = Path(filepath)
    all_file_paths = list(root_dir.rglob("*.*"))
    # text_splitter = RecursiveCharacterTextSplitter(
    #     chunk_size=1000,  # 每块目标长度（按字符或 token）
    #     chunk_overlap=100,  # 相邻块之间的重叠长度（缓解上下文断裂）
    #     length_function=len,  # 默认按字符计算长度；若用 token，需替换为 tokenizer
    #     separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    # )
    embedding = DashScopeEmbeddings(
        model="text-embedding-v3", dashscope_api_key=dashscope_api_key
    )
    milvus_vector = MilvusVector(collection_name="qa_vector", embedding=embedding)
    docs = []
    for i in tqdm(range(55)):
        for j in range(len(data[i])):
            # print(data[i][j]["data"][7:-3])
            if data[i][j]["data"] == "[]":
                continue
            str_message = data[i][j]["data"][7:-3]
            # print(str_message)
            if str_message.startswith("\n["):
                one_turn_all_qa = json.loads(str_message)
            else:
                one_turn_all_qa = json.loads("[{"+str_message+"}]")
            for one_qa in one_turn_all_qa:
                if "doctor_advice" in one_qa.keys() and "user_query" in one_qa.keys():
                    metadata = {}
                    metadata["doctor_advice"] = one_qa["doctor_advice"]
                    metadata['source'] = str(all_file_paths[i*16+j])[13:]
                    page_content = one_qa["user_query"]
                    docs.append(Document(page_content=page_content, metadata=metadata))
    
    # 手动batch
    bs = 64
    total_count = len(docs)
    start = 0
    for i in tqdm(range(int(total_count / bs)), "正在向量化并存储"):
        end = min(start + bs, total_count)
        batch_docs = docs[start:end]
        milvus_vector.vector_store(batch_docs)
        start = start + bs

    print("done!")