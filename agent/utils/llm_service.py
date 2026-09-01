"""LLM 服务封装
初始化和管理llm实例
"""

import os

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from config.app_config import configs


def _ensure_ssl_cert():
    ssl_cert = os.environ.get("SSL_CERT_FILE", "")
    if not ssl_cert or not os.path.isfile(ssl_cert):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass


_ensure_ssl_cert()


class LLMService:
    """LLM 服务封装类，负责初始化和管理 LLM 实例"""

    @staticmethod
    def create_llm() -> ChatOpenAI:
        """创建 LLM 实例

        Returns:
            ChatOpenAI: 配置好的 LLM 实例
        """
        dashscope_api_key = configs.llm.dashscope.api_key
        dashscope_api_base = configs.llm.dashscope.api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        return ChatOpenAI(
            base_url=dashscope_api_base,
            api_key=SecretStr(str(dashscope_api_key)),
            temperature=0.2,
            model=configs.llm.default or "qwen-plus",
            max_retries=3,
        )
