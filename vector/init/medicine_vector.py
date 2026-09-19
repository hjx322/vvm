###   ===========================================================================
###                                      文件说明
###   ===========================================================================
# __file__ = "medicine_vector.py"
# Description:该脚本用于实现 '药品库.xlsx' 信息的入库
# 单词嵌入大约 0.3亿 Token，按照官网给出text embedding v3的每千字 0.0005元 (2025年11月26日)
# 运行一次的成本大约是 15RMB，请谨慎修改这部分，尽量避免重复嵌入

import os
import statistics
from collections import Counter
from typing import Tuple

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from tqdm import tqdm

from config.app_config import configs
from data.datasets.medicine_dataset import (
    MedicineDataset,
    identification,
    medicine_dict,
)
from vector.milvus_vector import MilvusVector


# 分析数据长度分布,该函数可选调用，主要用于了解文本长度分布情况
# 示例: analyze_string_lengths(texts)
def analyze_string_lengths(str_list):
    """
    分析字符串列表的长度分布
    :param str_list: List[str]，例如你的 18000 个药品描述文本
    :return: None（打印结果）
    """
    if not str_list:
        print("输入列表为空")
        return

    # 1. 计算每个字符串的长度
    lengths = [len(s) for s in str_list]

    # 2. 基础统计
    min_len = min(lengths)
    max_len = max(lengths)
    mean_len = statistics.mean(lengths)
    median_len = statistics.median(lengths)

    print("📊 字符串长度分布统计")
    print(f"总数量: {len(str_list)}")
    print(f"最小长度: {min_len}")
    print(f"最大长度: {max_len}")
    print(f"平均长度: {mean_len:.2f}")
    print(f"中位数长度: {median_len}")

    # 3. 频次统计（按长度分组）
    length_counts = Counter(lengths)
    print("\n📈 长度频次 Top 20（按出现次数降序）:")
    for length, count in length_counts.most_common(20):
        print(f"  长度 {length:4d}: {count:4d} 条")

    # 4. 分桶统计（每 50 字符一个区间，适合长文本）
    print("\n📦 分桶统计（每 50 字符为一区间）:")
    bucket_size = 50
    max_bucket = ((max_len // bucket_size) + 1) * bucket_size
    buckets = {i: 0 for i in range(0, max_bucket + 1, bucket_size)}

    for L in lengths:
        bucket = ((L // bucket_size) + 1) * bucket_size
        if bucket > max_bucket:
            bucket = max_bucket
        # 找到对应桶的上限
        found = False
        for upper in buckets:
            if L < upper:
                buckets[upper] += 1
                found = True
                break
        if not found:
            buckets[max_bucket] += 1

    # 打印非零桶
    print("区间（字符数） | 数量")
    print("------------------")
    for upper, count in sorted(buckets.items()):
        if count > 0:
            lower = upper - bucket_size
            print(f"[{lower:4d}, {upper:4d}) | {count:5d}")


# 原始数据中，长度为732的信息有49条(最多)，前20频次长度都小于768，选择768可以极大程度保障药品信息的完整性
# 尽量还是只切分一些大块文本，避免切分过细导致语义不完整
def build_semantic_text(
    record: dict, chunk_max_length: int = 768
) -> Tuple[list[str], dict]:
    identification_parts = []
    description_all = []
    description_parts = []
    description_total_length = 0
    metadata_info = {}
    # 构建标识信息 + 描述信息
    for k, v in medicine_dict.items():
        if k in identification and record.get(v):
            identification_parts.append(f"{v}:{record[v]}")
            metadata_info[k] = record[v]
        if k not in identification and record.get(v):
            # 计算新增内容的长度（包括键名和分隔符）
            field_str = f"{v}:{record[v]}"
            field_len = len(field_str)

            # 如果加入后超限，先保存当前块（除非当前块为空）
            if (
                description_total_length + field_len > chunk_max_length
                and description_parts
            ):
                description_all.append(description_parts)
                description_parts = [field_str]
                description_total_length = field_len
            else:
                description_parts.append(field_str)
                description_total_length += field_len

    if description_parts:
        description_all.append(description_parts)

    # 如果没有任何描述字段，至少返回一个带标识的空描述
    if not description_all:
        return ["；".join(identification_parts)], metadata_info

    # 组合标识 + 每个描述块
    return [
        "；".join(identification_parts + part) for part in description_all
    ], metadata_info


if __name__ == "__main__":
    dashscope_api_key=configs.llm.dashscope.api_key
    file_name = "data/药品库.xlsx"
    mld = MedicineDataset(file_name)
    bs = 64
    docs = []

    ### 初始化切分器 考虑到描述信息在700左右的中位数，需要再加上标识信息，设定1000为chunk_size
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,  # 每块目标长度（按字符或 token）
        chunk_overlap=100,  # 相邻块之间的重叠长度（缓解上下文断裂）
        length_function=len,  # 默认按字符计算长度；若用 token，需替换为 tokenizer
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    texts = []
    texts_list = [build_semantic_text(rec) for rec in tqdm(mld)]
    # one_row_all_info:List[str] metadata:dict
    for one_row_all_info, metadata in tqdm(texts_list, desc="Processing files"):
        # one_chunks:str
        for one_chunks in one_row_all_info:
            # one_unit_all_chunks:List[str]
            one_unit_all_chunks = text_splitter.split_text(one_chunks)

            for chunk in one_unit_all_chunks:
                doc = Document(page_content=chunk, metadata=metadata)
                docs.append(doc)

    embedding = DashScopeEmbeddings(
        model="text-embedding-v3", dashscope_api_key=dashscope_api_key
    )
    milvus_vector = MilvusVector(collection_name="drug_vector", embedding=embedding)

    # 手动batch
    total_count = len(docs)
    start = 0
    for i in tqdm(range(int(total_count / bs)), "正在向量化并存储"):
        end = min(start + bs, total_count)
        batch_docs = docs[start:end]
        milvus_vector.vector_store(batch_docs)
        start = start + bs

    print("done!")
