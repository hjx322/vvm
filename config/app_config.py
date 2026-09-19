import argparse
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic_settings import BaseSettings


class Model(BaseSettings):
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    model: Optional[str] = None  # 模型名（用于 llm.roles.<role>.model 分级配置）

class Llm(BaseSettings):
    default: str
    dashscope: Model
    roles: Optional[Dict[str, Model]] = None  # 按角色分级模型（supervisor/safety/worker）


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


class MultiAgent(BaseSettings):
    """多 Agent（主管-子 agent）编排配置（恒开启，无 legacy 开关）"""

    worker_timeout: float = 60.0  # 单子 agent 技能执行超时（秒，透传给 dispatcher）
    management_user_id: str = "1827196"  # 子 agent-技能配置归属的管理租户（勿用患者号）
    # 各子 agent 开关（patient_knowledge/search/imaging/safety/general），由 build_all_agents 过滤；
    # 缺省 key=启用（保守：默认禁用会把病历查询静默关掉，医疗场景不可接受）
    agents: Dict[str, bool] = {}
    always_query_patient: bool = True  # 强制每次对话都查患者病历（supervisor 漏选时由 orchestrator 兜底补充）


class Emergency(BaseSettings):
    """急症前置硬规则配置（Fix: 命中即短路整张图，直接返回急诊指引）

    见 agent/utils/emergency.py。默认开启——医疗场景下这是安全底线，
    yaml 里显式写 enabled: false 才会关闭（例如做端到端评测时想跑完整链路）。
    """

    enabled: bool = True


class AppConfig(BaseSettings):
    llm: Llm
    db: Db
    vector: Vector
    asr: Asr
    search: Search
    db_crm: dict
    multi_agent: MultiAgent = MultiAgent()  # yaml 缺失时用默认（关闭）
    emergency: Emergency = Emergency()  # yaml 缺失时用默认（开启急症硬规则）

    @classmethod
    def from_yaml(cls, file_path: str) -> "AppConfig":
        import yaml
        # 先加载 .env（若有），再解析 yaml；解析后再把 ${VAR} 占位符换成真实值。
        # 这样 application.yaml 里只留占位符，密钥不落盘到受版本控制的文件里。
        load_dotenv(Path(file_path).parent / ".env")
        with open(file_path, "r", encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(**expand_env(data))


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


# ============================================================================
#  环境变量注入（密钥外置）
# ----------------------------------------------------------------------------
#  application.yaml 里写 ${VAR} 或 ${VAR:-默认值}，运行时替换成环境变量的值：
#
#      password: "${MYSQL_PASSWORD}"          # 未设置 → 展开成空串
#      api_key:  "${DASHSCOPE_API_KEY:-}"     # 显式给出空默认值
#
#  取值顺序：真实环境变量 > 项目根目录 .env 文件 > ${VAR:-后面的默认值}
# ============================================================================

# 匹配 ${NAME} 或 ${NAME:-默认值}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(env_path: Path) -> None:
    """把 .env 文件读进 os.environ（已存在的环境变量优先，不覆盖）。

    只支持最简语法：KEY=VALUE，忽略空行与 # 开头的注释行，
    值两端成对的引号会被去掉。不引入 python-dotenv 依赖，保持依赖树干净。
    """
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def expand_env(node: Any) -> Any:
    """递归地把配置树里的 ${VAR} / ${VAR:-默认值} 展开成环境变量的值。

    - 字符串：做替换
    - dict / list：递归处理
    - 其它类型（int/bool/None）：原样返回
    未设置且没写默认值时展开为空字符串 ""（故意如此：让配置项为空，
    好过把别人的密钥静默带进你的环境）。
    """
    if isinstance(node, str):
        def _replace(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_PATTERN.sub(_replace, node)
    if isinstance(node, dict):
        return {key: expand_env(value) for key, value in node.items()}
    if isinstance(node, list):
        return [expand_env(item) for item in node]
    return node



# 创建配置实例
configs = load_config()
