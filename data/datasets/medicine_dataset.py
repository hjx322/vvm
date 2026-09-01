###   ===========================================================================
###                                      文件说明
###   ===========================================================================
# __file__ = "medicine_dataset.py"
# Description:参考 '药品库说明.txt' 文件定义相关名称映射
# 注意：diseaseNames与上述说明文件中有出入，这个名称来自excel表头，说明中为diseases
#      但实际表头为diseaseNames，故以表头为准

import pandas as pd
import re
from typing import Dict, Any
from tqdm import tqdm

medicine_dict = {
    "_id": "id",
    "code": "药品标识",
    "frequentlyUse": "是否常用：0-不常用，1-常用",
    "comName": "药品名称",
    "chinesePinyin": "汉语拼音",
    "englishName": "英文名称",
    "shopName": "商品名称",
    "component": "成分",
    "property": "性状",
    "drugType": "药品类型",
    "ytime": "有效期",
    "standard": "执行标准",
    "pharmacology": "药理作用",
    "adverseReactions": "不良反应",
    "taboo": "禁忌",
    "notice": "注意事项",
    "medicFormat": "规格",
    "healthType": "医保类型",
    "storageMethod": "贮藏方法",
    "interactions": "药物相互作用",
    "recipeType": "处方类型",
    "price": "参考价格",
    "indication": "适应症",
    "dosage": "用法用量",
    "introduction": "介绍",
    "pharmacokinetics": "药代动力学",
    "diseaseNames": "相关疾病",
    "selectCount": "医生端查询次数",
}

# 标识信息与描述信息分离,identification是标识信息,不在这里的都是描述信息
identification = [
    "_id",
    "code",
    "comName",
    "chinesePinyin",
    "englishName",
    "shopName",
]

# 预编译正则表达式：匹配任意 HTML 标签（包括自闭合标签）
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def clean_html(text: str) -> str:
    """
    移除字符串中的所有 HTML 标签，返回纯文本。
    例如: "<p>阿司匹林</p>" → "阿司匹林"
    """
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    # 移除所有 <...> 标签
    clean = _HTML_TAG_PATTERN.sub("", text)
    # 可选：进一步清理多余空白（如多个空格、换行）
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# 设计成类别主要是考虑到后续有多模态需求，若采用传统读取方法，在后续添加其他模态信息可能需要重构数据加载类
class MedicineDataset:
    """
    药品数据库加载方法，从指定的 Excel 文件中读取药品信息，
    支持通过药品 ID 获取对应的药品信息字典。
    并且能够使用原生len方法获取药品记录总数。

    示例：
        if __name__ == "__main__":
            mdl = MedicineDataset("data/药品库.xlsx")
            print(f"药品库记录总数: {len(mdl)}")
            sample_id = mdl.get_all_ids()[0]
            print(f"示例药品 ID: {sample_id}")
            sample_record = mdl.get_item_by_id(sample_id)
            print("示例药品记录:")
            for k, v in sample_record.items():
                print(f"  {k}: {v}")
    """

    def __init__(self, medicine_file_path: str):
        self.df = pd.read_excel(medicine_file_path, dtype=str).fillna("")

        # 第一列作为 _id
        original_first_col = self.df.columns[0]
        self.df.rename(columns={original_first_col: "_id"}, inplace=True)

        # 去重
        if self.df["_id"].duplicated().any():
            print("⚠️ 警告：_id 列存在重复值，将保留首次出现的记录。")
            self.df = self.df.drop_duplicates(subset="_id", keep="first").reset_index(
                drop=True
            )

        # 清理所有单元格中的 HTML 标签（原地处理）
        self.df = self.df.map(clean_html)

        self._id_to_index = {
            row["_id"]: idx
            for idx, row in tqdm(self.df.iterrows(), "正在读取药品库数据")
        }  # 构建 _id 到行索引的映射
        self.en_to_zh = self.get_medicine_dict()

    def get_medicine_dict(self) -> Dict[str, str]:
        return medicine_dict

    def __len__(self) -> int:
        return len(self.df)

    def __iter__(self):
        for _, row in self.df.iterrows():
            yield {
                self.en_to_zh.get(en_key, en_key): value
                for en_key, value in row.to_dict().items()
            }

    def get_item_by_id(self, _id: str) -> Dict[str, Any]:
        if _id not in self._id_to_index:
            raise KeyError(f"药品 ID '{_id}' 不存在。")
        idx = int(self._id_to_index[_id])
        row_dict = self.df.iloc[idx].to_dict()

        # 转换 key 为中文
        result = {}
        for en_key, value in row_dict.items():
            zh_key = self.en_to_zh.get(en_key, en_key)
            result[zh_key] = value  # value 已经是 clean 后的纯文本
        return result

    def get_all_ids(self) -> list:
        return self.df["_id"].tolist()


if __name__ == "__main__":
    mdl = MedicineDataset("data/药品库.xlsx")
    print(f"药品库记录总数: {len(mdl)}")
    sample_id = mdl.get_all_ids()[0]
    print(f"示例药品 ID: {sample_id}")
    sample_record = mdl.get_item_by_id(sample_id)
    print("示例药品记录:")
    for k, v in sample_record.items():
        print(f"  {k}: {v}")
