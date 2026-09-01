from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class MySQLQuerySchema(BaseModel):
    medical_record_no: str = Field(description="患者病历号")
    crm: str = Field(description="CRM 数据库名")
    db_name: str = Field(description="MySQL 数据库名")


class MilvusSkillItem(BaseModel):
    name: Literal["drug_vector", "gastroenterology_vector", "disease_vector"] = Field(
        description="Milvus 集合名"
    )
    query: str = Field(description="Milvus 查询语句")


class MilvusQuerySchema(BaseModel):
    operation: Literal["search"] = Field("search", description="Milvus 操作类型，固定为search")
    skill_list: List[MilvusSkillItem] = Field(..., description="查询列表")


class WebSearchSchema(BaseModel):
    query: str = Field(description="搜索关键词")
    max_results: int = Field(default=5, description="返回的最大搜索结果数，范围1-20")
    search_depth: str = Field(default="advanced", description="搜索深度: basic 或 advanced")

class DermaImageSchema(BaseModel):
    model_name: Literal["YOLOv10.pt","YOLOv11.pt"] = Field(
        description="yolo皮肤病检测模型权重"
    )
    img_path: str = Field(description="检测图片路径")

