# -*- coding: utf-8 -*-
import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

from tencentcloud.asr.v20190614 import asr_client, models
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from config.app_config import configs


def _create_asr_client():
    """创建 ASR 客户端（同步，供 run_in_executor 使用）"""
    cred = credential.Credential(
        configs.asr.ten_secret_id, configs.asr.ten_secret_key
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "asr.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    return asr_client.AsrClient(cred, "ap-guangzhou", client_profile)


def _submit_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """同步提交 ASR 任务"""
    client = _create_asr_client()
    req = models.CreateRecTaskRequest()
    req.from_json_string(json.dumps(params))
    resp = client.CreateRecTask(req)
    return json.loads(resp.to_json_string())


def _query_task(task_id: str) -> Dict[str, Any]:
    """同步查询 ASR 任务状态"""
    client = _create_asr_client()
    req = models.DescribeTaskStatusRequest()
    req.TaskId = task_id
    resp = client.DescribeTaskStatus(req)
    return json.loads(resp.to_json_string())


async def asr_recognize(
    params: Dict[str, Any], poll_interval: float = 2.0, max_wait_time: float = 60.0
) -> Optional[Dict[str, Any]]:
    """
    异步提交录音文件进行 ASR 识别，并轮询结果。

    Args:
        params (dict): CreateRecTaskRequest 所需参数，如 EngineType, ChannelNum, ResTextFormat, SourceType, Url 或 Data 等。
        poll_interval (float): 查询间隔（秒），默认 2 秒。
        max_wait_time (float): 最大等待时间（秒），默认 60 秒。

    Returns:
        dict or None: 识别成功时返回包含 Result 的响应字典；超时或失败返回 None。
    """
    loop = asyncio.get_event_loop()

    try:
        # 提交任务
        resp = await loop.run_in_executor(None, _submit_task, params)
        task_id = resp.get("Data", {}).get("TaskId")
        if not task_id:
            print("❌ 未获取到 TaskId")
            return None

        # print(f"✅ 任务已提交，TaskId: {task_id}")

        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            await asyncio.sleep(poll_interval)
            result = await loop.run_in_executor(None, _query_task, task_id)
            status = result.get("Data", {}).get("Status")

            if status == 2:  # 成功
                # print("🎉 ASR 识别完成")
                return result
            elif status == 3:  # 失败
                print("❌ ASR 识别失败")
                return None
            # status == 0 或 1：排队或进行中，继续轮询

        print("⏰ ASR 识别超时")
        return None

    except TencentCloudSDKException as e:
        print(f"腾讯云 SDK 异常: {e}")
        return None
    except Exception as e:
        print(f"未知错误: {e}")
        return None


async def ten_asr(url):
    params = {
        "Url": url,
        "ChannelNum": 1,
        "EngineModelType": "16k_zh",
        "ResTextFormat": 1,
        "SourceType": 0,
    }

    result = await asr_recognize(params, poll_interval=2, max_wait_time=60)
    if result:
        # print("识别结果:", result["Data"]["Result"])
        return result["Data"]["Result"]
    else:
        print("未能获取识别结果")
        return None


if __name__ == "__main__":
    url = "http://qwcd.zdbantu.com/media/voice/2025/09/15/5b75f112c27a4d240974a63b4c4be91d.amr"
    asyncio.run(ten_asr(url))
