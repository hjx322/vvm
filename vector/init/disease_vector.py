########此段代码为对从网页上保存的表格数据进行清洗########

import re

import pandas as pd


def clean_disease_excel(input_path, output_path):
    """
    清洗疾病Excel数据，移除指定列中的<...>标签内容
    
    Args:
        input_path (str): 输入Excel文件路径
        output_path (str): 输出清洗后Excel文件路径
    
    Returns:
        pandas.DataFrame: 清洗后的DataFrame
    """
    # 1. 读取Excel
    try:
        df = pd.read_excel(input_path)
    except FileNotFoundError:
        print(f"错误：找不到文件 {input_path}")
        return None
    except Exception as e:
        print(f"读取文件时出错：{e}")
        return None
    
    # 2. 文本清洗方法：删除所有 <...> 内容
    def clean_text(text):
        if text is None or pd.isna(text):
            return ""
        text = str(text)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.strip()
        return text
    
    # 需要清洗的9个列
    cols_to_clean = [
        "everydayIntro",
        "pathogenesis",
        "preventionMeasures",
        "relatedDisease",
        "selectCount",
        "symptom",
        "symptomSummarize",
        "treatment",
        "treatready"
    ]
    
    # 3. 对每列进行处理（不会影响其它列）
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = df[col].astype(str).apply(clean_text)
        else:
            print(f"列不存在：{col}")
    
    df = df.fillna("")
    
    # 4. 保存清洗后的数据
    try:
        df.to_excel(output_path, index=False)
        print(f"清洗完成！已导出至：{output_path}")
    except Exception as e:
        print(f"保存文件时出错：{e}")
        return None
    
    return df

# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    # 定义输入输出路径
    input_excel = r"G:\python\company\vvm-demo-digital-smart-doctor-agent\vvm-demo-digital-smart-doctor-agent\diease.xlsx"
    output_excel = r"G:\python\company\vvm-demo-digital-smart-doctor-agent\cleaned_output_new2.xlsx"
    
    # 调用函数进行清洗
    cleaned_df = clean_disease_excel(input_excel, output_excel)
    
    # 可选：查看清洗后的前几行数据
    if cleaned_df is not None:
        print("\n清洗后的前5行数据：")
        print(cleaned_df.head())


################下面的代码进行将清洗后的数据向量化后存入milvus数据库################
import os
import re
import statistics
from collections import Counter

import pandas as pd
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from milvus_vector import MilvusVector
from tqdm import tqdm

EXCEL_PATH = r"G:\python\company\vvm-demo-digital-smart-doctor-agent\cleaned_output_new2.xlsx"

# ======================================================
# 字段名称映射表（中文标题）
# ======================================================
FIELD_NAME_MAP = {
    "_id": "_id",
    "code": "代码",
    "departmentName": "部门名称",
    "everydayIntro": "日常介绍",
    "name": "疾病名称",
    "otherName": "疾病别名",
    "pathogenesis": "发病机理",
    "preventionMeasures": "预防措施",
    "relatedDisease": "相关疾病",
    "selectCount": "数量",
    "symptom": "症状",
    "symptomSummarize": "症状总结",
    "treatment": "治疗方案",
    "treatready": "诊断指南",
}

# 主字段（用于合并文本）
MERGE_COLS = [
    "everydayIntro", "pathogenesis", "preventionMeasures",
    "relatedDisease", "symptom", "symptomSummarize",
    "treatment", "treatready"
]


# ======================================================
# 字符串长度分析函数
# ======================================================
def analyze_string_lengths(str_list):
    """
    分析字符串列表的长度分布
    :param str_list: List[str]，例如你的疾病描述文本
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

    print("\n" + "=" * 50)
    print("📊 合并后文本长度分布统计")
    print("=" * 50)
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

    # 4. 分桶统计（每 200 字符一个区间，适合长文本）
    print("\n📦 分桶统计（每 200 字符为一区间）:")
    bucket_size = 200
    max_bucket = ((max_len // bucket_size) + 1) * bucket_size
    buckets = {i: 0 for i in range(0, max_bucket + 1, bucket_size)}

    for L in lengths:
        for upper in sorted(buckets.keys()):
            if L < upper:
                buckets[upper] += 1
                break
        else:
            buckets[max_bucket] += 1

    # 打印非零桶
    print("区间（字符数） | 数量")
    print("------------------")
    for upper in sorted(buckets.keys()):
        if buckets[upper] > 0:
            lower = upper - bucket_size
            print(f"[{lower:4d}, {upper:4d}) | {buckets[upper]:5d}")

    # 5. 给出建议
    print("\n" + "=" * 50)
    print("💡 文本切分参数建议")
    print("=" * 50)
    if mean_len < 500:
        print("建议 chunk_size: 500, chunk_overlap: 50")
    elif mean_len < 1000:
        print("建议 chunk_size: 1000, chunk_overlap: 100")
    elif mean_len < 2000:
        print("建议 chunk_size: 1500, chunk_overlap: 200")
    else:
        print("建议 chunk_size: 2000, chunk_overlap: 300")

    # 基于最长文本的建议
    if max_len > 3000:
        print(f"注意：存在超长文本（{max_len}字符），建议增加 chunk_size 或检查数据质量")


# ======================================================
# 文本清理工具函数
# ======================================================
def clean_text(text):
    """彻底清理文本中的多余换行符和空白字符"""
    if pd.isna(text) or text is None:
        return ""

    # 转换为字符串
    text = str(text).strip()

    # 替换各种换行符（Windows \r\n, Unix \n, Mac \r）
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 移除连续的换行符（保留最多一个空行）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 移除行首行尾的空白字符
    lines = [line.strip() for line in text.split('\n')]

    # 过滤掉空行
    lines = [line for line in lines if line]

    # 重新合并为文本
    return '\n'.join(lines)


# ======================================================
# 智能文本切分函数（保证完整句子重叠）
# ======================================================
def split_text_with_complete_sentence_overlap(text, chunk_size=900, overlap_size=100):
    """
    将文本切分为块，每个块的开头包含上一个块结尾的完整句子
    :param text: 待切分的文本
    :param chunk_size: 每个块的目标大小
    :param overlap_size: 重叠区域的最小长度（用于寻找完整句子）
    :return: 切分后的文本块列表
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start_idx = 0
    text_length = len(text)

    # 中文句子结束符
    sentence_end_pattern = re.compile(r'([。！？；\n])')

    while start_idx < text_length:
        # 计算当前块的结束位置
        end_idx = start_idx + chunk_size

        # 如果是最后一块，直接取到末尾
        if end_idx >= text_length:
            chunk = text[start_idx:]
            chunks.append(chunk)
            break

        # 找到重叠区域内的最后一个句子结束符
        overlap_start = max(start_idx, end_idx - overlap_size)
        overlap_text = text[overlap_start:end_idx]

        # 查找所有句子结束符的位置
        end_matches = list(sentence_end_pattern.finditer(overlap_text))

        if end_matches:
            # 取最后一个结束符的位置作为实际结束点
            last_end_pos = end_matches[-1].end()
            actual_end_idx = overlap_start + last_end_pos
        else:
            # 如果没有找到结束符，直接用end_idx（兜底方案）
            actual_end_idx = end_idx

        # 提取当前块
        current_chunk = text[start_idx:actual_end_idx]
        chunks.append(current_chunk)

        # 更新起始位置（为下一个块留出重叠的完整句子）
        start_idx = actual_end_idx - overlap_size

        # 防止死循环（当文本无法切分时）
        if start_idx >= actual_end_idx:
            start_idx = actual_end_idx

    return chunks


# ======================================================
# 前缀字段
# ======================================================
PREFIX_KEYS = ["_id", "code", "name", "otherName"]


def build_prefix_metadata_text(row):
    """构造每个 chunk 顶部的基础信息前缀（合并为一行，用分号分隔）"""
    parts = []
    for key in PREFIX_KEYS:
        val = row.get(key, "")
        if pd.notna(val) and str(val).strip() != "":
            title = FIELD_NAME_MAP.get(key, key)
            parts.append(f"{title}: {val}")
    return "；".join(parts).strip()


# ======================================================
# 合并字段（使用中文标题）
# ======================================================
def merge_row_text(row):
    lines = []
    for col in MERGE_COLS:
        val = row.get(col, "")
        # 使用专门的清理函数处理每个字段值
        clean_val = clean_text(val)
        if clean_val:
            title = FIELD_NAME_MAP.get(col, col)
            lines.append(f"{title}: {clean_val}")

    # 合并为最终文本
    merged_text = "\n\n".join(lines)
    return merged_text


# ======================================================
# 读取 Excel
# ======================================================
df = pd.read_excel(EXCEL_PATH)
print(f"成功加载 Excel，共 {len(df)} 行，{len(df.columns)} 列。")

# ======================================================
# 处理文本并切分
# ======================================================
df["merged_text"] = df.apply(merge_row_text, axis=1)
print("合并文本完成！示例：")
print(df["merged_text"].iloc[0][:500])  # 显示更多内容便于检查

# ======================================================
# 分析合并后文本的长度分布
# ======================================================
analyze_string_lengths(df["merged_text"].tolist())

# ======================================================
# 构造 Documents
# ======================================================
docs = []
CHUNK_SIZE = 900
OVERLAP_SIZE = 150  # 增大重叠区域，确保能包含完整句子

for _, row in tqdm(df.iterrows(), total=len(df), desc="Building documents"):
    raw_text = row["merged_text"] or ""

    # 跳过空文本
    if not raw_text.strip():
        continue

    prefix_text = build_prefix_metadata_text(row)

    # 使用自定义的智能切分函数
    chunks = split_text_with_complete_sentence_overlap(
        raw_text,
        chunk_size=CHUNK_SIZE,
        overlap_size=OVERLAP_SIZE
    )

    # 调试：检查切块中的空行问题
    if len(chunks) > 0 and len(docs) < 3:
        print(f"\n调试 - 切块内容检查:")
        print(f"切块1前200字符: {chunks[0][:200]}")
        if len(chunks) > 1:
            print(f"切块2前200字符: {chunks[1][:200]}")
        print("-" * 50)

    for i, chunk in enumerate(chunks):
        # 最终清理：确保切块中没有多余空行
        clean_chunk = re.sub(r'\n+', '\n', chunk).strip()

        # 基础信息与正文直接用分号连接
        if prefix_text:
            chunk_with_prefix = f"{prefix_text}；{clean_chunk}"
        else:
            chunk_with_prefix = clean_chunk

        docs.append(
            Document(
                page_content=chunk_with_prefix,
                metadata={
                    "_id": row.get("_id"),
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "otherName": row.get("otherName"),
                    "departmentName": row.get("departmentName"),
                    "chunk_id": i,
                }
            )
        )

print(f"\n文档切片完成：共生成 {len(docs)} 个 chunks。")

# ======================================================
# 写入 Milvus
# ======================================================
vector_db = MilvusVector(collection_name="disease_vector")

print("开始向量化并写入 Milvus…")
ids = vector_db.vector_store(docs)
print(f"成功写入 {len(ids)} 条向量到 Milvus！")