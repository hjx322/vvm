"""Milvus Query Skill 实现及公共工具函数"""

import os
import sys
import json
from typing import Any, List, Tuple, Optional

sys.path.insert(0, os.path.abspath(".claude/skills"))
from milvus_query.scripts.search import search as milvus_search

from skills.skills_optimize_srh.base import SkillHandler, SkillResult, NecessaryDataResult
from .schemas import MilvusQuerySchema
from langchain_core.messages import SystemMessage, HumanMessage
from loguru import logger


# ===== 公共工具函数 =====

def format_milvus_content(content: Any) -> str:
    """将 Milvus 查询结果格式化为可读的字符串"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    lines = []
    for item in content:
        if not isinstance(item, dict):
            lines.append(str(item))
            continue
        collection = item.get("collection", "")
        query = item.get("query") or item.get("comName")
        if collection:
            lines.append(f"collection: {collection}")
        if query:
            lines.append(f"query: {query}")
        text_entries = item.get("text", [])
        if isinstance(text_entries, list):
            for index, entry in enumerate(text_entries, start=1):
                doc = None
                score = None
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    doc, score = entry[0], entry[1]
                else:
                    doc = entry
                if score is not None:
                    lines.append(f"[{index}] score: {score}")
                else:
                    lines.append(f"[{index}]")
                metadata = getattr(doc, "metadata", None)
                page_content = getattr(doc, "page_content", None)
                if metadata:
                    lines.append(f"metadata: {metadata}")
                if page_content:
                    lines.append(f"text: {page_content}")
        else:
            lines.append(f"text: {text_entries}")
        lines.append("----")
    return "\n".join(lines).strip()


# ===== 两阶段检索配置（硬编码，不由 LLM 定义） =====

RECALL_K = 10          # 第一阶段召回数量
RERANK_TOP_K = 3       # 第二阶段重排后返回数量
ENABLE_RERANKING = True  # 是否启用两阶段检索



class MilvusQuerySkill(SkillHandler):
    """从 Milvus 向量数据库检索医学知识"""

    schema = MilvusQuerySchema

    async def execute_with_llm_async(self, llm, messages) -> SkillResult:
        """
        使用 LLM 生成结构化参数并执行 Milvus 向量检索。

        Args:
            llm: 语言模型实例，需支持 with_structured_output 方法
            messages: 当前的对话历史列表

        Returns:
            SkillResult: 技能执行结果对象，包含 success 状态和 content 内容
        """
        try:
            structured_llm = llm.with_structured_output(self.schema)
            params_obj = structured_llm.invoke(messages)
            if not params_obj:
                return SkillResult(False, "LLM 返回了空的结构化输出")

            return self.call(params_obj.model_dump_json(), llm=llm, messages=messages)
        except Exception as e:
            return SkillResult(False, f"Milvus 参数生成失败: {str(e)}")

    def prepare_necessary_data(self, state) -> NecessaryDataResult:
        """Milvus 查询不需要必要数据"""
        return NecessaryDataResult(True, "")

    def call(self, input_param: str, llm=None, messages=None) -> SkillResult:
        """
        执行 Milvus 向量检索，使用硬编码的两阶段配置。

        Args:
            input_param: JSON 格式的输入参数
            llm: 可选，用于二阶段重排的 LLM 实例
            messages: 可选，对话历史（用于重排上下文）

        Returns:
            SkillResult: 执行结果
        """
        try:
            start = input_param.find("{")
            if start == -1:
                start = 0

            params_json = input_param[start:]
            params = json.loads(params_json)

            if ENABLE_RERANKING and llm and messages:
                # 两阶段模式：召回 + 重排
                content = self._search_with_rerank(params, llm, messages)
            else:
                # 单阶段模式：直接召回返回
                content = milvus_search(params_json)

            return SkillResult(
                success=True,
                content=format_milvus_content(content),
            )
        except Exception as e:
            logger.error(f"Milvus 查询执行失败: {str(e)}")
            return SkillResult(False, f"Milvus 查询失败: {str(e)}")

    def _search_with_rerank(self, params: dict, llm, messages: List) -> List[dict]:
        """
        两阶段检索：先召回再重排

        Args:
            params: 查询参数（包含 operation, skill_list）
            llm: LLM 实例
            messages: 对话历史

        Returns:
            重排后的结果列表
        """
        try:
            # 步骤1：执行第一阶段（recall）
            recall_results = self._execute_recall(params)

            # 步骤2：执行第二阶段（rerank）
            reranked_results = self._execute_rerank(recall_results, llm, messages)

            return reranked_results

        except Exception as e:
            logger.warning(f"两阶段检索失败，降级到单阶段: {str(e)}")
            try:
                recall_json = json.dumps(params)
                return milvus_search(recall_json)
            except Exception as fallback_e:
                logger.error(f"单阶段降级也失败: {str(fallback_e)}")
                return []

    def _execute_recall(self, params: dict) -> List[dict]:
        """
        第一阶段：执行召回
        使用硬编码的 RECALL_K 值

        Args:
            params: 查询参数

        Returns:
            包含所有召回结果的列表
        """
        skill_list = params.get("skill_list", [])

        # 为每个 skill 设置 recall_k（使用硬编码值）
        for skill in skill_list:
            skill["recall_k"] = RECALL_K  

        # 调用原有的 search 函数进行召回
        recall_json = json.dumps(params)
        recall_results = milvus_search(recall_json)

        logger.info(f"第一阶段（Recall）完成：使用 recall_k={RECALL_K}")
        return recall_results

    def _execute_rerank(self, recall_results: List[dict], llm, messages: List) -> List[dict]:
        """
        第二阶段：执行重排
        使用硬编码的 RERANK_TOP_K 值
        使用 LLM 对召回结果进行语义相关性评估和重排

        Args:
            recall_results: 第一阶段的召回结果
            llm: LLM 实例
            messages: 对话历史

        Returns:
            重排后的结果列表
        """
        reranked_results = []

        context = ""
        if messages and len(messages) >= 2:
            for msg in messages[-3:]:
                if hasattr(msg, 'content'):
                    context += f"{msg.__class__.__name__}: {msg.content[:100]}\n"

        for result_item in recall_results:
            collection = result_item.get("collection", "")
            query = result_item.get("query", "")
            text_entries = result_item.get("text", [])

            if not isinstance(text_entries, list) or len(text_entries) <= 1:
                reranked_results.append(result_item)
                continue

            try:
                reranked_entries = self._rerank_with_llm(
                    query=query,
                    docs=text_entries,
                    llm=llm,
                    context=context,
                    top_k=RERANK_TOP_K
                )

                reranked_results.append({
                    "collection": collection,
                    "query": query,
                    "text": reranked_entries,
                    "_metadata": {
                        "recall_count": len(text_entries),
                        "recall_k": RECALL_K,
                        "rerank_count": len(reranked_entries),
                        "rerank_top_k": RERANK_TOP_K,
                        "strategy": "two-stage-retrieval"
                    }
                })

                logger.info(f"集合 {collection} 重排完成：{len(text_entries)} → {len(reranked_entries)}")

            except Exception as e:
                logger.warning(f"集合 {collection} 重排失败，使用原始结果: {str(e)}")

                reranked_results.append({
                    "collection": collection,
                    "query": query,
                    "text": text_entries[:RERANK_TOP_K],
                    "_metadata": {
                        "recall_count": len(text_entries),
                        "recall_k": RECALL_K,
                        "rerank_count": min(RERANK_TOP_K, len(text_entries)),
                        "rerank_top_k": RERANK_TOP_K,
                        "strategy": "fallback"
                    }
                })

        logger.info(f"第二阶段（Rerank）完成：使用 rerank_top_k={RERANK_TOP_K}")
        return reranked_results

    def _rerank_with_llm(self, query: str, docs: List[Tuple],
                         llm, context: str, top_k: int = 3) -> List[Tuple]:
        """
        使用 LLM 对召回的文档进行重排

        Args:
            query: 原始查询
            docs: [(Document, score), ...] 形式的文档列表（已按向量相似度排序）
            llm: LLM 实例
            context: 对话上下文
            top_k: 返回的重排结果数

        Returns:
            重排后的文档列表（按相关性得分从高到低）
        """

        if len(docs) <= top_k:
            return docs

        doc_texts = []
        for i, item in enumerate(docs):
            doc = None
            score = None

            if isinstance(item, (list, tuple)) and len(item) >= 2:
                doc, score = item[0], item[1]
            else:
                doc = item
            page_content = getattr(doc, 'page_content', str(doc))
            if len(page_content) > 400:
                page_content = page_content[:400] + "..."

            doc_texts.append(f"【{i+1}】{page_content}")

        context_str = f"\n【对话背景】：{context}" if context else ""

        rerank_prompt = f"""您是一位医学知识库检索评估专家。

【用户查询】：{query}{context_str}

【检索结果】（共{len(docs)}条，按向量相似度排序）：
{chr(10).join(doc_texts)}

【任务】：
1. 忽略向量相似度排序，仅从语义相关性评估
2. 从上述结果中选出最相关的{min(top_k, len(docs))}条
3. 按相关性从高到低排序

【返回格式】（严格JSON）：
{{
    "selected_indices": [{', '.join(str(i) for i in range(min(top_k, len(docs))))}],
    "reason": "简短的重排理由"
}}

只返回JSON，不要其他内容。"""

        try:
            response = llm.invoke([
                SystemMessage(content="你是医学知识专家。请严格按照JSON格式返回结果。"),
                HumanMessage(content=rerank_prompt)
            ])

            response_text = response.content

            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}") + 1

            if start_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                result_json = json.loads(json_str)

                selected_indices = result_json.get("selected_indices", [])

                reranked = []
                for idx in selected_indices:
                    if isinstance(idx, int) and 0 <= idx < len(docs):
                        reranked.append(docs[idx])

                if reranked:
                    logger.debug(f"LLM 重排成功: {len(docs)} → {len(reranked)}")
                    return reranked

            logger.warning(f"LLM 响应解析失败，使用原始结果")
            return docs[:top_k]

        except Exception as e:
            logger.warning(f"LLM 重排异常: {str(e)}，使用原始结果")
            return docs[:top_k]
