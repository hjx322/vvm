import os

import pandas as pd
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from config.app_config import configs

# Milvus 配置
MILVUS_HOST = configs.vector.host
MILVUS_PORT = configs.vector.port
MILVUS_USER = configs.vector.user
MILVUS_PASSWORD = configs.vector.password
MILVUS_DB = configs.vector.db_name

def connect_milvus():
    """连接 Milvus 数据库"""
    try:
        # 建立连接
        conn_params = {
            "host": MILVUS_HOST,
            "port": MILVUS_PORT,
            "db_name": MILVUS_DB
        }
        # 如果开启了用户认证，添加用户名密码
        if MILVUS_USER and MILVUS_PASSWORD:
            conn_params["user"] = MILVUS_USER
            conn_params["password"] = MILVUS_PASSWORD
        
        connections.connect(**conn_params)
        print(f"成功连接到 Milvus: {MILVUS_HOST}:{MILVUS_PORT}")
    except Exception as e:
        print(f"连接 Milvus 失败: {e}")
        raise

def get_collection_schema(collection_name):
    """获取 Collection 的 Schema 信息"""
    try:
        # 检查 Collection 是否存在
        if not utility.has_collection(collection_name):
            raise ValueError(f"Collection '{collection_name}' 不存在")
        
        # 获取 Collection 对象
        collection = Collection(collection_name)
        # 获取 Schema
        schema = collection.schema
        return schema, collection
    except Exception as e:
        print(f"获取 Collection Schema 失败: {e}")
        raise

def export_collection_to_excel(collection_name, output_file="milvus_export.xlsx", batch_size=1000):
    """
    导出 Milvus Collection 数据到 Excel 文件
    
    Args:
        collection_name: 要导出的 Collection 名称
        output_file: 输出的 Excel 文件路径
        batch_size: 批量查询的大小（避免一次性查询过多数据导致内存溢出）
    """
    # 1. 连接 Milvus
    connect_milvus()
    
    # 2. 获取 Schema 和 Collection 对象
    schema, collection = get_collection_schema(collection_name)
    
    # 3. 获取所有字段名称（排除向量字段的原始数据，只保留可序列化的字段）
    field_names = []
    vector_fields = []
    for field in schema.fields:
        field_names.append(field.name)
        if field.dtype in [DataType.FLOAT_VECTOR, DataType.BINARY_VECTOR]:
            vector_fields.append(field.name)
    
    # 4. 加载 Collection（确保数据可查询）
    collection.load()
    
    # 5. 获取总数据量
    total_count = collection.num_entities
    print(f"Collection '{collection_name}' 总数据量: {total_count}")
    
    if total_count == 0:
        print("Collection 中无数据，无需导出")
        return
    
    # 6. 批量查询数据
    all_data = []
    for start in range(0, total_count, batch_size):
        end = min(start + batch_size, total_count)
        print(f"查询数据范围: {start} - {end}")
        
        # 查询数据（_id 是 Milvus 自动生成的主键）
        query_result = collection.query(
            expr="",  # 空表达式表示查询所有数据
            output_fields=field_names,
            limit=batch_size,
            offset=start
        )
        
        # 处理向量字段（将向量转换为字符串，方便 Excel 存储）
        for record in query_result:
            for vec_field in vector_fields:
                if vec_field in record:
                    record[vec_field] = str(record[vec_field])
            all_data.append(record)
    
    # 7. 转换为 DataFrame 并保存为 Excel
    df = pd.DataFrame(all_data)
    df.to_excel(output_file, index=False, engine="openpyxl")
    print(f"数据已成功导出到: {output_file}")
    

if __name__ == "__main__":
    # 配置要导出的 Collection 名称和输出文件路径
    TARGET_COLLECTION = "qa_vector"  # 替换为你的 Collection 名称
    OUTPUT_EXCEL = "qa_vector_data.xlsx"  # 输出文件路径
    
    # 执行导出
    try:
        export_collection_to_excel(
            collection_name=TARGET_COLLECTION,
            output_file=OUTPUT_EXCEL,
            batch_size=1000
        )
    except Exception as e:
        print(f"导出失败: {e}")