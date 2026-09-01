###   ===========================================================================
###                                      文件说明
###   ===========================================================================
# __file__ = "dialog_dataset.py"
# Description:该文件处理河南皮肤医生的对话数据，该文件能够自动处理在dialogs文件夹下的对话
# 但严格依赖表头顺序，请参考给出文件的表头，或对代码内容进行修改
# 若添加新的医生对话数据，请手动维护greeting和doctor俩个list
import asyncio
import random
import re
from asyncio import Semaphore
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Literal

import pandas as pd
from pandas import DataFrame
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio

from data.datasets.ten_asr import ten_asr


async def limited_ten_asr(url: str, semaphore: Semaphore):
    async with semaphore:  # 获取一个“许可”，最多 concurrent_limit 个协程能同时进入
        return await ten_asr(url)


# 预定义打招呼的内容，后续处理过程中直接删除，后续添加新的对话的时候需要更新这个list
greetings = [
    "你好",
    "您好",
    "我已经添加了你，现在我们可以开始聊天了。",
    "我已經添加了你，現在我們可以開始聊天了。",
    "您好，我是孙会娟医生",
    "您好，我是皮肤科中西医结合专科医师，是你的主治医生",
    "您好，我是主任医师刘高岗",
    "你好，我是你的主治医生翟留洋医生",
    "您好，我是你的皮肤科专科主治医生",
    "您好，我是主治医师李浩",
    "您好，我是主治医师刘高岗",
    "新的一年，向您及家人致以最诚挚的祝福！",
]

# 手动维护医生list,确保对话过程中不出现角色乱窜
doctor = [
    "田新科",
    "孙会娟",
    "刘高岗",
    "翟医生",
    "翟留洋",
    "李浩",
    "刘高岗",
]


def format_url(text):
    return "http:" + str(text.replace(":", "", 1))


def is_greeting(text):
    if not isinstance(text, str):
        return False
    for kw in greetings:
        if kw in text:
            return True
    return False


def is_doctor(text):
    if not isinstance(text, str):
        return False
    for kw in doctor:
        if kw in text:
            return True
    return False


class DialogDataset:
    """
    用于读取对话数据的方法
    get_dialogs：通过路径访问所有的excel表格，并且根据表头信息将内容进行初步格式化
    clean_greetings_and_soliloquize：先清除所有的打招呼语，详见greeting，然后清除所有只有单向发送信息的对话
    make_full_message：获取完整的对话信息并格式化为json格式
    make_only_text_message：仅获取文本模态的对话信息并格式化为json格式
    make_qa_format：将message按照不同的方法进行QA的格式化
    make_other_message：预留接口，用于后续其他模态的扩充，可参考上述两种message函数逇写法
    get_greeting_list：获取打招呼语列表
    get_doctor_list：获取医生姓名列表
    """

    def __init__(self, file_path):
        self.dialogs = DataFrame()
        self.filelist = set()
        self.dialogs = self.get_dialogs(file_path)
        self.clean_greetings_and_soliloquize()

    def get_dialogs(self, file_path) -> Dict[str, DataFrame]:
        dialogs = {}
        ### 使用Path读取所有文件路径
        root_dir = Path(file_path)
        all_file_paths = list(root_dir.rglob("*.*"))
        if not all_file_paths:
            print(f"在路径 {file_path} 下未找到任何文件，请检查路径是否正确。")
            exit(1)
        for one_file_path in tqdm(all_file_paths, "正在读取对话"):
            dialogs[one_file_path.name] = pd.read_excel(
                one_file_path,
                dtype=str,
                header=2,
            ).fillna("")
        return dialogs

    def clean_greetings_and_soliloquize(self):
        dialogs = self.dialogs
        valid_dialogs = pd.DataFrame()  # 初始化为空 DataFrame

        for filename in dialogs.keys():
            context = dialogs[filename]
            if context is None or context.empty:
                continue

            # 转换时间列为 datetime
            time_col = context.columns[0]
            context[time_col] = pd.to_datetime(context[time_col], errors="coerce")

            sender_col = context.columns[1]
            content_col = context.columns[3]

            # >>>>>>>>>> 第一步：先过滤问候语 <<<<<<<<<<
            non_greeting_mask = ~context[content_col].apply(is_greeting)
            cleaned_context = context[non_greeting_mask].reset_index(drop=True)
            # 如果删完后对话为空，直接跳过
            if cleaned_context.empty:
                continue

            # >>>>>>>>>> 第二步：再判断发送人是否多样 <<<<<<<<<<
            if cleaned_context[sender_col].nunique(dropna=True) == 1:
                # print(f"文件 {filename}：删除问候语后发送人全部相同，对话已舍弃。")
                continue
            # 保留有效对话
            cleaned_context["filename"] = filename
            self.filelist.add(filename)
            valid_dialogs = pd.concat(
                [valid_dialogs, cleaned_context], ignore_index=True
            )

        if not valid_dialogs.empty:
            # 源文件按时间降序，将最终结果反转为升序（按时间正序）
            self.dialogs = valid_dialogs.iloc[::-1].reset_index(drop=True)
        else:
            self.dialogs = pd.DataFrame()

    def make_full_message(self):
        return asyncio.run(self.make_full_message_async())

    def make_only_text_message(self) -> Dict:
        return asyncio.run(self.make_only_text_message_async())

    def make_audio_and_text_message(self):
        return asyncio.run(self.make_audio_and_text_message_async())

    def make_qa_format(
        self, type: Literal["full", "text", "audio"] = "text"
    ) -> list[str]:
        # 1. 选择对应的异步构建函数
        if type == "text":
            make_message_coro = self.make_only_text_message_async()
        elif type == "full":
            make_message_coro = self.make_full_message_async()
        elif type == "audio":
            make_message_coro = self.make_audio_and_text_message_async()
        else:
            raise ValueError("错误的指定类型，请参考 make_qa_format 参数")

        # 2. 在单个事件循环中运行异步构建
        text_message = asyncio.run(make_message_coro)

        # 3. 后处理（同步，无性能问题）
        file_list = self.filelist
        all_dialogs = []
        for f in file_list:
            try:
                one_turn_dialogs = text_message[f]
            except KeyError:
                print(f"{f} 该文件无意义，跳过")
                continue

            dialog = ""
            for message in one_turn_dialogs:
                for key, value in message.items():
                    if key in ("Doctor", "User"):
                        dialog += f"\n{key}:{value}"
            dialog += "\n"
            all_dialogs.append(dialog)

        return all_dialogs

    def get_greeting_list(self):
        return greetings

    def get_doctor_list(self):
        return doctor

    def __len__(self):
        return len(self.dialogs)

    async def make_audio_and_text_message_async(self, max_concurrent: int = 10):
        semaphore = asyncio.Semaphore(max_concurrent)
        # 第一阶段：收集所有消息元数据 + 构建 ASR 任务
        asr_futures = []  # 存放 ten_asr(url) 返回的 coroutine
        asr_metadata = []  # 存放对应元信息：(conv_id, char, time_str)
        non_asr_messages = []  # 存放无需 ASR 的消息
        error_file = []
        
        for one_message in self.dialogs.itertuples(name=None):
            conv_id = one_message[-1]
            time_str = str(one_message[1])
            char = "Doctor" if is_doctor(one_message[2]) else "User"

            msg_type = one_message[5]
            if msg_type == "文本":
                content = one_message[4]
                non_asr_messages.append((conv_id, char, content, time_str))
            elif msg_type in ("语音", "音频存档"):
                url = format_url(one_message[6])
                # 仅创建 coroutine，不立即 await
                try:
                    asr_futures.append(limited_ten_asr(url, semaphore))
                    asr_metadata.append((conv_id, char, time_str))
                except:
                    error_file.append(url)
                    continue
            else:
                continue

        # 第二阶段：并发执行所有 ASR 任务
        messages = defaultdict(list)

        if asr_futures:
            results = await tqdm_asyncio.gather(
                *asr_futures, desc="ASR 语音识别中", total=len(asr_futures)
            )

            # 处理 ASR 结果
            for result, (conv_id, char, time_str) in zip(results, asr_metadata):
                if result is None:
                    content = "[ASR 失败]"
                else:
                    content = re.sub(r"\[[^\]]*\]\s*", "", result).strip()
                messages[conv_id].append({char: content, "time": time_str})

        # 第三阶段：加入非 ASR 消息
        for conv_id, char, content, time_str in non_asr_messages:
            messages[conv_id].append({char: content, "time": time_str})
        print(error_file)
        return messages

    async def make_full_message_async(self, max_concurrent: int = 10):
        semaphore = asyncio.Semaphore(max_concurrent)
        # 第一阶段：收集所有消息元数据 + 构建 ASR 任务
        asr_futures = []  # 存放 ten_asr(url) 返回的 coroutine
        asr_metadata = []  # 存放对应元信息：(conv_id, char, time_str)
        non_asr_messages = []  # 存放无需 ASR 的消息

        for one_message in self.dialogs.itertuples(name=None):
            conv_id = one_message[-1]
            time_str = str(one_message[1])
            char = "Doctor" if is_doctor(one_message[2]) else "User"

            msg_type = one_message[5]
            if msg_type == "文本":
                content = one_message[4]
                non_asr_messages.append((conv_id, char, content, time_str))
            elif msg_type in ("语音", "音频存档"):
                url = format_url(one_message[6])
                # 仅创建 coroutine，不立即 await
                asr_futures.append(limited_ten_asr(url, semaphore))
                asr_metadata.append((conv_id, char, time_str))
            else:
                content = {one_message[4]: format_url(one_message[6])}
                non_asr_messages.append((conv_id, char, content, time_str))

        # 第二阶段：并发执行所有 ASR 任务
        messages = defaultdict(list)

        if asr_futures:
            # 可选：限制并发数（防 API 限流）
            # 使用 asyncio.Semaphore 控制并发（见下方补充）
            results = await tqdm_asyncio.gather(
                *asr_futures, desc="ASR 语音识别中", total=len(asr_futures)
            )

            # 处理 ASR 结果
            for result, (conv_id, char, time_str) in zip(results, asr_metadata):
                if result is None:
                    content = "[ASR 失败]"
                else:
                    content = re.sub(r"\[[^\]]*\]\s*", "", result).strip()
                messages[conv_id].append({char: content, "time": time_str})

        # 第三阶段：加入非 ASR 消息
        for conv_id, char, content, time_str in non_asr_messages:
            messages[conv_id].append({char: content, "time": time_str})

        return messages

    async def make_only_text_message_async(self):
        messages = defaultdict(list)
        for one_message in self.dialogs.itertuples(name=None):
            if one_message[5] != "文本":
                continue  # 只保留文本
            char = "Doctor" if is_doctor(one_message[2]) else "User"
            content = one_message[4]
            messages[one_message[-1]].append(
                {char: content, "time": str(one_message[1])}
            )
        return messages


# 这里得用多进程，单线程跑1290个文件，纯音频得43小时，加上入库时间估计得50小时
# 因为涉及一些在线操作，不使用dataloader的话一旦断线就彻底重来
# 这地方网络也不太稳定，强烈建议用dataloader处理所有的在线数据
# 八字硬可直接传完整数据集
# PS:在线数据处理都是收费服务
class DialogDataloader:
    def __init__(
        self,
        filepath: str,
        type: str = "audio",
        batch_size: int = 16,
        # 这里不是机器学习，shuffle一般false
        shuffle: bool = False,
        collate_fn: Callable = None,
    ) -> None:
        # 1. 加载数据集
        self.filepath = filepath
        self.type = type
        self.dataset: List[str] = DialogDataset(filepath).make_qa_format(type)

        # 2. 配置参数
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.collate_fn = collate_fn or self._default_collate

    def _default_collate(self, batch: List[str]) -> List[str]:
        """默认 collate：返回字符串列表（适用于文本对话）"""
        return batch

    def __len__(self) -> int:
        """返回 batch 的数量（向上取整）"""
        return (len(self.dataset) + self.batch_size - 1) // self.batch_size

    def __iter__(self) -> Iterator[Any]:
        indices = list(range(len(self.dataset)))
        if self.shuffle:
            random.shuffle(indices)

        batch = []
        for idx in indices:
            batch.append(self.dataset[idx])  # self.dataset 是 List[str]
            if len(batch) == self.batch_size:
                yield self.collate_fn(batch)
                batch = []

        # 处理最后一个不完整的 batch
        if batch:
            yield self.collate_fn(batch)


# 简单测试
if __name__ == "__main__":

    # 创建 dataloader，只加载 audio + text 转写后的对话
    loader = DialogDataloader(filepath="data/test_dialog")

    for batch in tqdm(loader):
        print(f"Batch size: {len(batch)}")
        print(batch[0])
