import sys
from contextlib import contextmanager
from pathlib import Path

root_path = str(Path(__file__).parent.parent.parent.parent.parent)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from config.app_config import configs

# =========================
# 数据库 河南CRM
# =========================
MYSQL_URL_HN = configs.db_crm["hn"]
engine_hn = create_engine(
    MYSQL_URL_HN,
    echo=False,  # True 可打印 SQL 调试
    pool_size=20,  # 连接池最大连接数
    max_overflow=40,  # 超出连接池大小后允许的额外连接数
    pool_pre_ping=True,  # 自动检查失效连接（获取连接前执行 SELECT 1）
    pool_recycle=3600,  # 连接回收周期（秒），防止长连接被服务器关闭
    pool_timeout=60,  # 获取连接超时时间（秒）
    connect_args={
        'connect_timeout': 10,  # TCP连接超时时间（秒）
        'read_timeout': 60,     # 读取超时时间（秒）
        'write_timeout': 60,    # 写入超时时间（秒）
    }
)

SessionDBHn = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine_hn))

BaseCRM = declarative_base()

DB_SESSION_MAP = {
    "hn": SessionDBHn
}


@contextmanager
def get_crm_db(crm: str):
    """获取数据库会话的上下文管理器

    使用示例：
        with get_crm_db("hn") as session:
            result = session.query(Model).filter(...).all()

    特性：
        - pool_pre_ping=True: 获取连接前自动检查（SELECT 1）
        - pool_recycle=3600: 1小时后自动回收连接
        - pool_timeout=60: 获取连接最多等待60秒
        - connect_args 配置各项超时参数
    """
    session_factory = DB_SESSION_MAP[crm]
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()
