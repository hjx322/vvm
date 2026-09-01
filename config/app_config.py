import argparse
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Model(BaseSettings):
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class Llm(BaseSettings):
    default: str
    dashscope: Model


class Mysql(BaseSettings):
    host: str
    username: str
    password: str
    port: int
    db: str


class Db(BaseSettings):
    mysql: Mysql
    patient_mysql: Mysql


class IndexParams(BaseSettings):
    """索引参数配置"""
    nlist: Optional[int] = None  # 稠密向量聚类数
    drop_ratio_build: Optional[float] = None  # 稀疏向量构建时的丢弃比率
    nprobe: Optional[int] = None  # 向量检索聚类数


class IndexConfig(BaseSettings):
    """单个索引配置 (dense/sparse/search)"""
    metric_type: str  # 距离度量类型，如 "IP" (内积)
    index_type: Optional[str] = None  # 索引类型，如 "IVF_FLAT" 或 "SPARSE_INVERTED_INDEX"
    params: IndexParams


class Index(BaseSettings):
    """向量索引配置"""
    dense: IndexConfig  # 稠密向量索引配置
    sparse: IndexConfig  # 稀疏向量索引配置
    search: IndexConfig  # 搜索配置


class Vector(BaseSettings):
    """Milvus 向量数据库配置"""
    host: str
    port: str
    user: str
    password: str
    db_name: str

class Asr(BaseSettings):
    ten_secret_id: str
    ten_secret_key: str


class Tavily(BaseSettings):
    api_key: str


class Search(BaseSettings):
    tavily: Tavily


class AppConfig(BaseSettings):
    llm: Llm
    db: Db
    vector: Vector
    asr: Asr
    search: Search
    db_crm: dict

    @classmethod
    def from_yaml(cls, file_path: str) -> "AppConfig":
        import yaml
        with open(file_path, "r", encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(**data)


@lru_cache
def load_config() -> AppConfig:
    # 解析命令行参数
    run_env = parse_env()
    home_path = Path(__file__).parent.parent
    file_name = "application.yaml"
    file_path = os.path.join(home_path, file_name)
    app_config = AppConfig.from_yaml(file_path)
    # 加载配置信息到环境变量
    # load_config_to_env(app_config)
    return app_config


def load_config_to_env(config: AppConfig):
    flat_config = flatten_dict(config.model_dump())
    for key, value in flat_config.items():
        os.environ[key.upper()] = str(value)


def flatten_dict(d, parent_key='', sep='_'):
    """
    递归地将嵌套字典展平为单层字典。
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def parse_env() -> str:
    """ 解析命令行参数 """
    import sys
    if "uvicorn" in sys.argv[0]:
        # 使用uvicorn启动时，命令行参数只能按照uvicorn的文档来，不能传自定义参数，否则报错
        return "dev"
    # 使用 argparse 定义命令行参数
    parser = argparse.ArgumentParser(description="命令行参数")
    parser.add_argument("--env", type=str, default="", help="运行环境")
    # 解析命令行参数
    args = parser.parse_args()
    return args.env


# 创建配置实例
configs = load_config()
