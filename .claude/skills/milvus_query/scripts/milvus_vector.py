### copy from vvm-agent-platform-srv/core/vector/vector_db.py
### 将参数改为从 application.yaml（配合 .env）获取，实例化时自动构建 embedding 对象
import sys
from pathlib import Path
from typing import List, Tuple

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus.vectorstores.milvus import Milvus
from pydantic import SecretStr

# 安全修复：原先这里硬编码了 DashScope API Key、Milvus 主机/口令（会随仓库一起上传）。
# 现改为统一从 config.app_config 读取，真实值只存在于本地 .env / 环境变量中。
# 本文件位于 <项目根>/.claude/skills/milvus_query/scripts/，parents[4] 即项目根。
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.app_config import configs


class MilvusVector:
    def __init__(
        self,
        collection_name: str = "",
        embedding: Embeddings = DashScopeEmbeddings(
            model="text-embedding-v3", dashscope_api_key=configs.llm.dashscope.api_key
        ),
        host: str = configs.vector.host,
        port: int = int(configs.vector.port),
        username: str = configs.vector.user,
        password: str = configs.vector.password,
        db: str = configs.vector.db_name,
    ):
        self.embedding = embedding
        self.collection_name = collection_name
        self.con_args = {
            "uri": f"http://{host}:{port}",
            "token": f"{username}:{password}",
            "db_name": db,
        }
        # 默认值是  {'index_type': 'HNSW', 'metric_type': 'COSINE', 'params': {'M': 8, 'efConstruction': 64}}
        self.index_params = {
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {
                "M": 16,  # 修改 HNSW 的 M 值
                "efConstruction": 128,  # 修改 efConstruction 值
            },
        }
        self.search_params = {
            "metric_type": "COSINE",
            "params": {"ef": 10},  # ef 是 HNSW 搜索时的候选队列大小
        }

    def vector_store(self, docs: List[Document]) -> List[str]:
        texts = [d.page_content for d in docs]
        metadatas = [d.metadata for d in docs]
        index_ids = self._vector_db.add_texts(texts, metadatas)
        return index_ids

    def vector_del(self, expr: str):
        """
        param expr: 查询milvus的过滤条件 参考 https://milvus.io/docs/zh/boolean.md
        """
        self._vector_db.delete(expr=expr)

    def similarity_search(
        self, query: str, k: int, expr: str = ""
    ) -> List[Tuple[Document, float]]:
        """
        :param query: 查询文本
        :param k: 返回的相似文档数量
        :param expr: 查询milvus的过滤条件 参考 https://milvus.io/api-reference/pymilvus/v2.5.x/ORM/Collection/search.md
        这里因为调用技能的时候需要更换collection name所以延迟到查询前再注册_vector_db，这里可以优化，但请确保运行正常
        """

        self._vector_db: Milvus = Milvus(
            self.embedding,
            connection_args=self.con_args,
            collection_name=self.collection_name,
            auto_id=True,
            index_params=self.index_params,
            search_params=self.search_params,
        )

        return self._vector_db.similarity_search_with_score(query, k, expr=expr)

    def get_by_pk(self, _id: str) -> Document | None:
        try:
            search_result = self.similarity_search("", 1, f"pk == {_id}")
            return search_result[0][0]
        except Exception as e:
            return None
