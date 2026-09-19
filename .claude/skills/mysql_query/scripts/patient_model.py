from mysql_db import BaseCRM
from sqlalchemy import BigInteger, Date, DateTime, Double, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column


def pub_to_str(data: BaseCRM) -> str:
    gender_map = {0: "未知", 1: "男", 2: "女"}
    skip_attr = ["id"]
    parts = []

    for attr_name, attr_value in data.__mapper__.c.items():
        if attr_name in skip_attr:
            continue
        # attr_value 是 Column 对象
        comment = attr_value.comment or attr_name  # 使用 comment，没有就用字段名
        value = getattr(data, attr_name, None)

        # 特殊处理 gender
        if attr_name == "gender":
            comment = "性别"
            value = gender_map.get(value, "未知")
        # 日期类型可以格式化成字符串
        if hasattr(value, "strftime"):
            value = value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "hour") else value.strftime("%Y-%m-%d")
        # None 处理为空字符串
        if value is None:
            value = ""

        parts.append(f"{comment}:{value}")

    return "，".join(parts)


class PatientInfo(BaseCRM):
    __tablename__ = 'cst_patient_info'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, nullable=False, comment='主键ID')
    medical_record_no: Mapped[str] = mapped_column(String(20), nullable=True, default='', comment='病历号')
    name: Mapped[str] = mapped_column(String(30), nullable=False, default='', comment='姓名')
    phone: Mapped[str] = mapped_column(String(50), nullable=True, comment='手机号')
    birthday: Mapped[str] = mapped_column(Date, nullable=True, comment='生日')
    gender: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, comment='性别:0-未知 1-男 2-女')
    file_date: Mapped[str] = mapped_column(DateTime, nullable=True, comment='建档日期')
    address: Mapped[str] = mapped_column(String(255), nullable=True, default='', comment='家庭住址')

    def to_str(self) -> str:
        return pub_to_str(self)


class PatientVisitRecord(BaseCRM):
    __tablename__ = 'cst_patient_visit_record'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, nullable=False, comment="主键")
    visit_date: Mapped[Date | None] = mapped_column(Date, nullable=True, comment="就诊日期")
    medical_record_no: Mapped[str] = mapped_column(String(50), nullable=False, default="", comment="病历号")
    visit_doctor: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="就诊医生")
    diags: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="诊断")
    consumption_amount: Mapped[float | None] = mapped_column(Double, nullable=True, comment="消费金额")

    def to_str(self) -> str:
        return pub_to_str(self)


class PatientExamineResult(BaseCRM):
    __tablename__ = "cst_examine_result_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, nullable=False, comment="主键")
    seq: Mapped[str] = mapped_column(String(100), nullable=False, comment="批次流水号")
    medical_record_no: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="病历号")
    name: Mapped[str | None] = mapped_column(String(4000), nullable=True, comment="病人姓名")
    gender: Mapped[str | None] = mapped_column(String(2), nullable=True, comment="病人性别")
    age: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="年龄")
    clinical_diagnosis: Mapped[str | None] = mapped_column(String(127), nullable=True, comment="临床诊断")
    entered_by: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="录入人")
    reviewed_by: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="审核人")
    content: Mapped[str | None] = mapped_column(String(400), nullable=True, comment="检验内容")
    item_cn: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="检验项目中文全称")
    result: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="检验结果")
    result_status: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="检验结果状态")
    completion_time: Mapped[DateTime] = mapped_column(DateTime, nullable=False, comment="完成时间")
    reference_interval: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="参考区间")
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="单位")

    def to_str(self) -> str:
        return pub_to_str(self)
