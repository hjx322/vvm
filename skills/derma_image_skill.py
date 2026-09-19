import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod

import json
import time
import os
from ultralytics import YOLO

import cv2

from skills.schemas import DermaImageSchema
from skills.skills_optimize_srh.base import SkillHandler, SkillResult, NecessaryDataResult



class ImagePredictor:
    def __init__(self, weights_path, img_path, save_path="./runs/result.jpg", conf=0.5):
        """
        初始化ImagePredictor类
        :param weights_path: 权重文件路径
        :param img_path: 输入图像路径
        :param save_path: 结果保存路径
        :param conf: 置信度阈值
        """
        self.model = YOLO(weights_path)
        self.conf = conf
        self.img_path = img_path
        self.save_path = save_path
        self.labels = ['鲍温病', '基底细胞癌', '良性角化病病变', '皮肤纤维瘤', '黑色素瘤', '黑素细胞痣', '血管病变']

    def predict(self):
        """
        预测图像并保存结果
        """
        start_time = time.time()  # 开始计时

        # 执行预测
        results = self.model(source=self.img_path, conf=self.conf, half=True, save_conf=True)

        end_time = time.time()  # 结束计时
        elapsed_time = end_time - start_time  # 计算用时

        # 初始化默认结果
        all_results = {
            'labels': [],  # 存储所有标签
            'confidences': [],  # 存储所有置信度
            'allTime': f"{elapsed_time:.3f}秒"
        }

        try:
            # 检查是否有检测结果
            if len(results) == 0:
                print("未检测到目标，保存原始图片。")
                # 保存原始图片
                self._save_original_image()
                return all_results

            has_detection = False

            for result in results:
                # 提取置信度和标签
                confidences = result.boxes.conf if hasattr(result.boxes, 'conf') else []
                labels = result.boxes.cls if hasattr(result.boxes, 'cls') else []

                # 检查 confidences 和 labels 是否为空
                if confidences.numel() > 0 and labels.numel() > 0:
                    has_detection = True
                    # 获取标签名称和对应置信度
                    label_names = [self.labels[int(cls)] for cls in labels]
                    predictions = list(zip(label_names, confidences))

                    # 将每个结果保存到字典中
                    for label, conf in predictions:
                        all_results['labels'].append(label)
                        all_results['confidences'].append(f"{conf * 100:.2f}%")

                # 无论是否有检测结果，都保存图片
                result.save(filename=self.save_path)

            if not has_detection:
                print("未检测到目标，但已保存图片。")

            return all_results  # 返回包含标签和置信度的字典

        except Exception as e:
            # 如果预测过程中发生异常，打印错误信息并尝试保存原始图片
            print(f"预测过程中发生异常: {e}")
            try:
                self._save_original_image()
            except:
                print("保存原始图片失败")
            return all_results

    def _save_original_image(self):
        """
        保存原始图片到指定路径
        """
        import cv2
        import os

        # 确保保存目录存在
        os.makedirs(os.path.dirname(self.save_path), exist_ok=True)

        # 读取原始图片并保存
        img = cv2.imread(self.img_path)
        if img is not None:
            cv2.imwrite(self.save_path, img)
            print(f"原始图片已保存到: {self.save_path}")
        else:
            print("无法读取原始图片")



class BaseDermaHandler(SkillHandler):
    """
    Derma 项目通用功能混入类 (Mixin)
    实现了通用的参数解析和结果格式化，减少子类重复代码
    """
    def parse_params(self, input_param: str) -> Dict[str, Any]:
        """统一解析 JSON 和 Markdown 代码块"""
        try:
            clean = input_param.strip()
            # 去除 Markdown 代码块标记
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
            
            # 尝试解析 JSON
            if clean.startswith("{"):
                return json.loads(clean)
            # 容错：纯字符串视为路径——但必须是单行、无空白分隔的合法路径，
            # 避免把 LLM 生成的自由文本（多行/超长）误当图片路径，
            # 否则会产生"文件不存在: <一整段人话>"式的污染日志（Fix: 重试参数污染兜底）
            if "\n" not in clean and len(clean) < 200 and " " not in clean.strip():
                return {"_raw_path": clean}
            raise ValueError("参数解析异常: 非 JSON 文本不可作为图片路径")
        except Exception as e:
            raise ValueError(f"参数解析异常: {str(e)}")
    def format_success_msg(self, task_name: str, result: Dict) -> str:
        """统一生成自然语言回复"""
        time_cost = result.get("allTime", "")
        labels = result.get("labels", [])
        output = result.get("output_path", "")
        
        msg = f"{task_name}完成 (耗时{time_cost}"
        if "frame_count" in result:
            msg += f", 处理{result['frame_count']}帧"
        msg += ")。"
        
        if labels:
            msg += f" 发现目标: {', '.join(labels)}。"
        else:
            msg += " 未发现明显异常。"
            
        msg += f"\n结果已保存至: {output}"
        return msg
        
class BaseYOLOSkill:
    """YOLO 核心逻辑基类：负责模型加载与配置管理"""
    def __init__(self):
        self.is_initialized = True
        self.weights_path: Optional[str] = None
        self.conf: float = 0.5
        self.labels = ['鲍温病', '基底细胞癌', '良性角化病病变', '皮肤纤维瘤', '黑色素瘤', '黑素细胞痣', '血管病变']
        self.recording = False

    def initialize(self, weights_path: str, conf: float = 0.5):
        if not os.path.exists(weights_path):
            raise FileNotFoundError(f"权重缺失: {weights_path}")
        self.weights_path = weights_path
        self.conf = conf
        self.is_initialized = True

    def _get_model(self, weights_path_arg: Optional[str] = None):
        """懒加载模型获取器"""
        path = weights_path_arg or self.weights_path
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"模型文件无效: {path}")
        from ultralytics import YOLO
        return YOLO(path), os.path.basename(path)



class ImagePredictionSkill(BaseYOLOSkill):
    """
    图片检测技能
    注：此处保留手动构建返回结果的逻辑，以兼容 utils.predictImg 的行为
    """
    def execute(self, img_path: str, save_path: str, weights_path="./.claude/skills/derma_image/references/weights/YOLOv10.pt", conf=None):
        try:
            if not os.path.exists(img_path): 
                raise FileNotFoundError(f"文件不存在: {img_path}")
            
            
            # 复用原有的 ImagePredictor
            predictor = ImagePredictor(
                weights_path=weights_path, 
                img_path=img_path, 
                save_path=save_path, 
                conf=conf if conf is not None else self.conf
            )
            
            # 获取原始预测结果
            results = predictor.predict()
            
            # 【关键兼容修正】确保返回格式与旧代码完全一致
            return {
                "success": True,
                "labels": results.get("labels", []),
                "confidences": results.get("confidences", []),
                "allTime": results.get("allTime", "0.000秒"),
                "output_path": save_path,
                "model": os.path.basename(weights_path)
            }
        except Exception as e:
            return {"success": False, "error": str(e), "allTime": "0s"}


class ImageDetectHandler(BaseDermaHandler):
    """图片检测处理器（结果输出目录：derma_image/）"""
    _WEIGHTS_DIR = "./.claude/skills/derma_image/references/weights"
    _RUNS_DIR = "./derma_image"
    schema = DermaImageSchema
    requires_image = True  # Fix: 缺少真实图片路径时由 dispatcher 跳过，避免无图片被误调度
    
    def __init__(self):

        self.skill = ImagePredictionSkill()
        self.default_weights = os.getenv("YOLO_WEIGHTS_PATH", f"{self._WEIGHTS_DIR}/YOLOv12.pt")
        if os.path.exists(self.default_weights):
            self.skill.initialize(self.default_weights)

    def prepare_necessary_data(self, state: Dict) -> NecessaryDataResult:
        if self.skill.is_initialized:
            return NecessaryDataResult(True, "图片服务就绪")
        if os.path.exists(self.default_weights):
            self.skill.initialize(self.default_weights)
            return NecessaryDataResult(True, "图片服务已初始化")
        return NecessaryDataResult(False, "未初始化: 权重文件缺失（可放置 YOLOv8/v10/v11/v12.pt 到 derma_image/weights/）")
    
    async def execute_with_llm_async(self, llm, messages) -> SkillResult:
        """
        使用 LLM 生成结构化参数并执行 MySQL 查询。

        Args:
            llm: 语言模型实例，需支持 with_structured_output 方法
            messages: 当前的对话历史列表

        Returns:
            SkillResult: 技能执行结果对象，包含 success 状态和 content 内容
        """
        try:
            structured_llm = llm.with_structured_output(self.schema)
            params_obj = structured_llm.invoke(messages)
            if not params_obj:
                return SkillResult(False, "LLM 返回了空的结构化输出")
            return self.call(params_obj.model_dump_json())
        except Exception as e:
            return SkillResult(False, f"MySQL 参数生成失败: {str(e)}")

    def call(self, input_param: str) -> SkillResult:
        try:
            p = self.parse_params(input_param)
            path = p.get("img_path") or p.get("_raw_path")
            if not path: return SkillResult(False, "缺少 img_path")
            os.makedirs(f"{self._RUNS_DIR}", exist_ok=True)
            # ./.claude/skills/derma_image/references/weights

            
            weights_path = f"{self._WEIGHTS_DIR}/{p['model_name']}" if p.get("model_name") else None
            res = self.skill.execute(path, f"{self._RUNS_DIR}/current_result.jpg", weights_path=weights_path, conf=p.get("conf"))
            
            if not res.get("success"):
                return SkillResult(False, res.get("error"))
            
            return SkillResult(
                success=True, 
                content=self.format_success_msg("图片检测", res), 
                raw=res
            )
        except Exception as e:
            return SkillResult(False, str(e))
        


if __name__ == '__main__':
    # 初始化预测器
    predictor = ImagePredictor("../weights/helmet_best.pt", "../test.jpg", save_path="../runs/result.jpg", conf=0.1)

    # 执行预测
    result = predictor.predict()
    labels_str = json.dumps(result['labels'], ensure_ascii=False)  # 确保中文字符正常显示
    confidences_str = json.dumps(result['confidences'], ensure_ascii=False)
    print(f"检测标签: {labels_str}")
    print(f"置信度: {confidences_str}")
    print(f"检测用时: {result['allTime']}")
    print(f"结果图片已保存到: {predictor.save_path}")