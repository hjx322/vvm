import sys
import time
from typing import Sequence
from pathlib import Path
import json
from typing import Dict, Any
from loguru import logger

mysql_scripts_path = str(Path(__file__).parent)
if mysql_scripts_path not in sys.path:
    sys.path.insert(0, mysql_scripts_path)

from mysql_db import get_crm_db
from sqlalchemy import select

from patient_model import PatientExamineResult, PatientInfo, PatientVisitRecord

def load_input(raw: str) -> Dict[str, Any]:
    """解析 --input JSON 字符串"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}")

def eprint(*args):
    """输出到 stderr"""
    print(*args, file=sys.stderr)

def search_with_retry(input: str, max_retries: int = 3) -> str:
    """带重试机制的MySQL查询

    Args:
        input: JSON格式的查询参数
        max_retries: 最大重试次数

    Returns:
        查询结果字符串
    """
    retry_count = 0
    last_error = None

    while retry_count < max_retries:
        try:
            return search(input)
        except Exception as e:
            last_error = e
            retry_count += 1

            # 检查是否是连接断开的错误
            error_msg = str(e).lower()
            is_connection_error = any(keyword in error_msg for keyword in [
                'gone away', '2006', '2013', 'connection reset',
                'connection refused', 'lost connection', 'deadlock'
            ])

            if is_connection_error and retry_count < max_retries:
                wait_time = 2 ** retry_count  # 指数退避: 2, 4, 8 秒
                logger.warning(
                    f"数据库连接错误，{wait_time}秒后进行第 {retry_count} 次重试: {e}"
                )
                time.sleep(wait_time)
            elif retry_count >= max_retries:
                logger.error(f"MySQL查询失败，已重试 {max_retries} 次，放弃: {e}")
                break
            else:
                logger.error(f"MySQL查询失败: {e}")
                break

    # 所有重试都失败
    eprint(f"[search.py ERROR] 所有重试均失败 - {last_error}")
    raise last_error

def search(input) -> str:
    """执行MySQL查询

    Args:
        input: JSON格式的查询参数，包含:
            - medical_record_no: 患者病历号
            - crm: CRM数据库名
            - db_name: 表名 (patient_info, visit_record_list, examine_result_list)

    Returns:
        查询结果字符串

    Raises:
        Exception: 查询失败时抛出异常
    """
    try:
        data = load_input(input)
        medical_record_no = str(data.get("medical_record_no"))
        crm = str(data.get("crm"))
        db_name = str(data.get("db_name"))

        logger.info(f"Executing MySQL query: 表名={db_name}, 病历号={medical_record_no}, CRM={crm}")

        with get_crm_db(crm) as patient_db:
            if db_name == "patient_info":
                patient_info: PatientInfo = patient_db.scalars(
                    select(PatientInfo).where(
                        PatientInfo.medical_record_no == medical_record_no
                    )
                ).one_or_none()

                if patient_info:
                    logger.info(f"成功查询患者信息: {medical_record_no}")
                    return patient_info.to_str()
                else:
                    logger.warning(f"未找到患者信息: {medical_record_no}")
                    return f"未找到病历号为 {medical_record_no} 的患者信息"

            elif db_name == "visit_record_list":
                visit_record_list: Sequence[PatientVisitRecord] = patient_db.scalars(
                    select(PatientVisitRecord).where(
                        PatientVisitRecord.medical_record_no == medical_record_no
                    )
                ).all()

                if visit_record_list:
                    logger.info(f"成功查询患者就诊记录: {medical_record_no}，共 {len(visit_record_list)} 条")
                    return "\n".join([item.to_str() for item in visit_record_list])
                else:
                    logger.warning(f"未找到患者就诊记录: {medical_record_no}")
                    return f"未找到病历号为 {medical_record_no} 的就诊记录"

            elif db_name == "examine_result_list":
                examine_result_list: Sequence[PatientExamineResult] = patient_db.scalars(
                    select(PatientExamineResult).where(
                        PatientExamineResult.medical_record_no == medical_record_no
                    )
                ).all()

                if examine_result_list:
                    logger.info(f"成功查询患者检查结果: {medical_record_no}，共 {len(examine_result_list)} 条")
                    return "\n".join([item.to_str() for item in examine_result_list])
                else:
                    logger.warning(f"未找到患者检查结果: {medical_record_no}")
                    return f"未找到病历号为 {medical_record_no} 的检查结果"
            else:
                logger.error(f"无效的表名: {db_name}")
                return f"错误的db_name: {db_name}，请检查参数是否正确"

    except Exception as e:
        logger.error(f"MySQL查询异常: {e}", exc_info=True)
        eprint(f"[search.py ERROR] {e}")
        raise

def get_patient_info_main():
    """主函数示例"""
    try:
        patient_info = search(json.dumps({
            "medical_record_no": "1827196",
            "crm": "hn",
            "db_name": "patient_info"
        }))
        print(patient_info)
    except Exception as e:
        eprint(f"[search.py ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    get_patient_info_main()