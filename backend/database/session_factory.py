#fastapi连接mysql数据集
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

from config.app_config import AppConfig


_SessionLocal = None


def init_db_session(config: AppConfig = None):
    """Initialize database session factory"""
    global _SessionLocal

    if config is None:
        from config.app_config import configs as _configs
        config = _configs

    mysql_config = config.db.mysql
    db_user = mysql_config.username
    db_pass = mysql_config.password
    db_host = mysql_config.host
    db_port = mysql_config.port
    db_name = mysql_config.db

    if os.getenv("TEST_ENV") == "true":
        database_url = "sqlite:///:memory:"
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        if db_pass:
            database_url = f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        else:
            database_url = f"mysql+pymysql://{db_user}@{db_host}:{db_port}/{db_name}"

        engine = create_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
            pool_pre_ping=True,
            connect_args={
                "charset": "utf8mb4",
                "autocommit": True,
                "connect_timeout": 10,
            },
        )

    from backend.models import Base
    Base.metadata.create_all(bind=engine)

    # expire_on_commit=False：get_session_context 退出时 commit+close 后，
    # 路由仍可读取服务层返回的 ORM 实例字段（否则 detached 对象触发 refresh 报错）
    _SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return engine


def get_session() -> Session:
    """Get database session"""
    global _SessionLocal

    if _SessionLocal is None:
        raise RuntimeError("Database session not initialized. Call init_db_session() first.")

    return _SessionLocal()


@contextmanager
def get_session_context():
    """Context manager for database session"""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
