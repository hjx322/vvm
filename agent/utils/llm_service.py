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

    @staticmethod
    def create_agent_llm(role: str) -> ChatOpenAI:
        """按角色创建分级 LLM 实例（多 Agent 改造：Supervisor / Worker 各用独立模型）

        Args:
            role: 模型分级键（supervisor/safety/worker）。
                  对应 application.yaml 的 llm.roles.<role>.{model,temperature}；
                  未配置时回退到 llm.default（qwen-plus）+ temperature 0.2。

        Returns:
            ChatOpenAI: 该角色专用 LLM 实例
        """
        dashscope_api_key = configs.llm.dashscope.api_key
        dashscope_api_base = configs.llm.dashscope.api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        # P1 才加入 llm.roles 配置，这里用 getattr 兼容（未配置 roles 时走默认）
        roles = getattr(configs.llm, "roles", None) or {}
        role_cfg = roles.get(role) or {}
        model = getattr(role_cfg, "model", None) or configs.llm.default or "qwen-plus"
        temperature = getattr(role_cfg, "temperature", None)
        if temperature is None:
            temperature = 0.2 if role != "supervisor" else 0.0

        return ChatOpenAI(
            base_url=dashscope_api_base,
            api_key=SecretStr(str(dashscope_api_key)),
            temperature=temperature,
            model=model,
            max_retries=3,
        )
