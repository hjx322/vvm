# -*- coding: utf-8 -*-
"""查询数据库中的技能"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.app_config import load_config
from backend.models import Skill

config = load_config()
mysql_config = config.db.mysql

db_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}:{mysql_config.port}/{mysql_config.db}"
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
db_session = SessionLocal()

try:
    # 查询所有技能
    skills = db_session.query(Skill).all()
    print("数据库中的技能：")
    for skill in skills:
        print(f"- {skill.skill_id}: {skill.description[:50] if skill.description else 'N/A'}")

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db_session.close()
