#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from typing import Any, Dict, List
import sys
from pathlib import Path
from loguru import logger
milvus_scripts_path = str(Path(__file__).parent)
if milvus_scripts_path not in sys.path:
    sys.path.insert(0, milvus_scripts_path)

from milvus_vector import MilvusVector

from pymilvus import Collection, connections

# =========================
# 基础配置
# =========================
milvusdb = MilvusVector()

# =========================
# 工具函数
# =========================

def eprint(*args):
    """输出到 stderr"""
    print(*args, file=sys.stderr)


def load_input(raw: str) -> Dict[str, Any]:
    """解析 --input JSON 字符串"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}")


# =========================
# Milvus 查询占位函数
# =========================

def search_drug_vector(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    drug_vector:
      - 使用 comName / englishName / chinesePinyin 等字段做过滤或语义检索
      - 返回 text 中的药物说明信息
    """
    # 优先使用 query，但也兼容 comName
    query = params.get("query") or params.get("comName") or ""
    logger.info(f"search_drug_vector query: {query}")

    # 支持可配置的 top_k（用于重排场景）
    # 如果未指定 top_k，使用默认值 2；如果指定了 recall_k，则使用 recall_k
    recall_k = params.get("recall_k")
    if recall_k is not None and recall_k > 0:
        top_k = recall_k  # 两阶段模式：召回更多结果
    else:
        top_k = params.get("top_k", 2)  # 单阶段模式：原有默认值

    milvusdb.collection_name = "drug_vector"
    information = milvusdb.similarity_search(query=query, k=top_k)
    return [
        {
            "collection": "drug_vector",
            "query": query,
            "text": information
        }
    ]


def search_gastroenterology_vector(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    gastroenterology_vector:
      - 仅基于 text + vector 的医学知识检索
    """
    # 优先使用 query，但也兼容 text
    query = params.get("query") or params.get("text")
    logger.info(f"search_gastroenterology_vector query: {query}")

    if not query:
        raise ValueError("gastroenterology_vector requires `query`")

    # 支持可配置的 top_k（用于重排场景）
    recall_k = params.get("recall_k")
    if recall_k is not None and recall_k > 0:
        top_k = recall_k  # 两阶段模式：召回更多结果
    else:
        top_k = params.get("top_k", 5)  # 单阶段模式：原有默认值

    milvusdb.collection_name = "gastroenterology_vector"
    information = milvusdb.similarity_search(query=query, k=top_k)

    return [
        {
            "collection": "gastroenterology_vector",
            "query": query,
            "text": information
        }
    ]


def search_disease_vector(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    # 优先使用 query，但也兼容 text
    query = params.get("query") or params.get("text")
    logger.info(f"search_disease_vector query: {query}")

    if not query:
        raise ValueError("disease_vector requires `query`")

    # 支持可配置的 top_k（用于重排场景）
    recall_k = params.get("recall_k")
    if recall_k is not None and recall_k > 0:
        top_k = recall_k  # 两阶段模式：召回更多结果
    else:
        top_k = params.get("top_k", 3)  # 单阶段模式：原有默认值

    milvusdb.collection_name = "disease_vector"
    information = milvusdb.similarity_search(query=query, k=top_k)

    return [
        {
            "collection": "disease_vector",
            "query": query,
            "text": information
        }
    ]

def dispatch_search(skill: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = skill.get("name")
    if name == "drug_vector":
        return search_drug_vector(skill)
    elif name == "gastroenterology_vector":
        return search_gastroenterology_vector(skill)
    elif name == "disease_vector":
        return search_disease_vector(skill)
    else:
        raise ValueError(f"Unknown collection name: {name}")

def search(input):
    try:
        data = load_input(input)
        if data.get("operation") != "search":
            raise ValueError("Only operation='search' is supported")

        skill_list = data.get("skill_list")
        if not isinstance(skill_list, list):
            raise ValueError("`skill_list` must be a list")

        all_results: List[Dict[str, Any]] = []

        for skill in skill_list:
            results = dispatch_search(skill)
            all_results.extend(results)  
        return all_results
        # print(all_results)

    except Exception as e:
        # stderr：错误信息
        eprint(f"[search.py ERROR] {e}")
        sys.exit(1)

# # 示例代码
# cmd = '{"operation":"search", "skill_list":[{"name":"drug_vector", "comName":"阿莫西林"}]}'
# print(search(cmd))