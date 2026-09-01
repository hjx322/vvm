"""技能调度器新旧执行路径延迟基准测试（方案 b：先实测再写）

复测对象（与设计文档 skills_optimize_srh.md 的示例一致）：
    clawhub_weather-cn（自定义技能，execution.yaml 声明 subprocess_script 直跑）

测量点：
  [1]  新路径 _execute_custom_script()  —— subprocess 直跑 weather-cn.sh（N=5，原始整句入参）
  [1b] 新路径 · 干净入参 —— 参数已前置提炼为"西安"，直跑执行机制本身（N=5）
  [2]  旧路径 _execute_external_skill() —— LLM 工具循环（N=3，附每轮 LLM 迭代数）
  [3]  Tier1 关键词匹配 vs Tier3 LLM 结构化选择 时延对比

复用现有生产函数（agent.utils.llm_service / skills.skills_optimize_srh.*），
不改动核心逻辑。运行：.venv/Scripts/python benchmark_skill_latency.py
    或：uv run python benchmark_skill_latency.py
"""

import asyncio
import json
import os
import re
import statistics
import sys
import time

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.utils.llm_service import LLMService
from skills.skills_optimize_srh.manifest import SkillManifest
from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher

SKILL_NAME = "clawhub_weather-cn"
USER_ID = "1827196"           # 仓库内真实租户目录
HUMAN_INPUT = "西安的天气怎么样"
CITY_ONLY = "西安"            # 前置提炼后的干净入参（old 路径由 LLM 提炼出该值）
ROUNDS_NEW = 5   # 新路径便宜，多跑
ROUNDS_OLD = 3   # 旧路径耗 token，少跑

_RESULTS = {}


def _round3(x: float) -> float:
    return round(x, 3)


def _report(name: str, times: list) -> None:
    mean = statistics.mean(times)
    median = statistics.median(times)
    minimum = min(times)
    maximum = max(times)
    _RESULTS[name] = {
        "n": len(times),
        "mean": _round3(mean),
        "median": _round3(median),
        "min": _round3(minimum),
        "max": _round3(maximum),
    }
    print(
        f"  {name}: n={len(times)}  mean={mean:.2f}s  median={median:.2f}s  "
        f"min={minimum:.2f}s  max={maximum:.2f}s"
    )


def _count_llm_iterations(logs: list) -> int:
    """从 loguru 捕获信息中统计该轮 LLM 工具循环的迭代数"""
    for m in logs:
        if "工具循环结束" in m or "达到最大轮数" in m:
            nums = re.findall(r"（(\d+) 轮）", m)
            if nums:
                return int(nums[0])
    return None


async def main() -> None:
    llm = LLMService.create_llm()
    disp = UnifiedSkillDispatcher(llm)
    state = {
        "user_id": USER_ID,
        "medical_record_no": USER_ID,
        "human_input": HUMAN_INPUT,
    }

    # 0) 把天气技能 manifest 注入注册表（新/旧路径与 Tier3 选择都能命中）
    base_dir = os.path.realpath(os.path.join("user_skills", USER_ID, SKILL_NAME, "current"))
    yml = os.path.join(base_dir, "execution.yaml")
    manifest = (
        SkillManifest.from_yaml(yml)
        if os.path.exists(yml)
        else SkillManifest.from_skill_md(os.path.join(base_dir, "SKILL.md"))
    )
    manifest.base_dir = base_dir
    disp.registry.register_custom(USER_ID, manifest)
    print(
        f"manifest: {manifest.name} runner={manifest.runner} "
        f"entrypoint={manifest.entrypoint}"
    )

    # ---------------------------------------------------------------
    # [1] 新路径 · 原始整句入参（复现生产直跑行为）
    # ---------------------------------------------------------------
    print("\n[1] 新路径 _execute_custom_script（subprocess 直跑，整句入参）")
    new_times = []
    new_ok = 0
    for i in range(ROUNDS_NEW):
        t0 = time.perf_counter()
        res = await disp._execute_custom_script(SKILL_NAME, state, HUMAN_INPUT)
        dt = time.perf_counter() - t0
        new_times.append(dt)
        if res.get("success"):
            new_ok += 1
        print(f"    round{i + 1}: {dt:.2f}s  success={res.get('success')}")
    _report("new_path_raw", new_times)
    _RESULTS["new_path_raw_ok"] = f"{new_ok}/{ROUNDS_NEW}"

    # ---------------------------------------------------------------
    # [1b] 新路径 · 干净入参（参数已前置提炼），衡量执行机制本身
    # ---------------------------------------------------------------
    print(f"\n[1b] 新路径 · 干净入参（{CITY_ONLY}）——参数已提炼，脚本直跑")
    clean_times = []
    clean_ok = 0
    for i in range(ROUNDS_NEW):
        t0 = time.perf_counter()
        res = await disp.registry._execute_script(manifest, CITY_ONLY, USER_ID)
        dt = time.perf_counter() - t0
        clean_times.append(dt)
        if res.get("success"):
            clean_ok += 1
        print(f"    round{i + 1}: {dt:.2f}s  success={res.get('success')}")
    _report("new_path_clean_arg", clean_times)
    _RESULTS["new_path_clean_ok"] = f"{clean_ok}/{ROUNDS_NEW}"

    # ---------------------------------------------------------------
    # [2] 旧路径：LLM 工具循环（附每轮 LLM 迭代次数统计）
    # ---------------------------------------------------------------
    print("\n[2] 旧路径 _execute_external_skill（LLM 工具循环）")
    old_times = []
    old_iters = []
    loop_msgs = []

    def _capture(msg: str) -> None:
        loop_msgs.append(msg)

    sink_id = logger.add(_capture, level="INFO", format="{message}")

    for i in range(ROUNDS_OLD):
        loop_msgs.clear()
        t0 = time.perf_counter()
        try:
            res = await disp._execute_external_skill(SKILL_NAME, HUMAN_INPUT, state)
            dt = time.perf_counter() - t0
            old_times.append(dt)
            n_it = _count_llm_iterations(loop_msgs)
            old_iters.append(n_it)
            print(
                f"    round{i + 1}: {dt:.2f}s  success={res.get('success')}  "
                f"llm_iterations={n_it}"
            )
        except Exception as e:  # noqa: BLE001
            print(f"    round{i + 1}: 失败 {type(e).__name__}: {e}")
            old_times.append(None)
            old_iters.append(None)

    logger.remove(sink_id)

    valid_old = [t for t in old_times if t is not None]
    if valid_old:
        _report("old_path_exec", valid_old)
    else:
        print("  [!] 旧路径全部失败，未测得有效延迟")

    # ---------------------------------------------------------------
    # [3] Tier1 关键词匹配 vs Tier3 LLM 结构化选择
    # ---------------------------------------------------------------
    print("\n[3] 技能选择阶段：Tier1 关键词规则 vs Tier3 LLM 结构化输出")

    t0 = time.perf_counter()
    hits = disp.registry.match(HUMAN_INPUT, USER_ID)
    dt1 = time.perf_counter() - t0
    print(f"    Tier1 registry.match: {dt1 * 1000:.2f} ms  命中={hits}")
    _RESULTS["tier1_ms"] = _round3(dt1 * 1000)

    t0 = time.perf_counter()
    try:
        sel = await disp.select_skills(HUMAN_INPUT, state)
        dt3 = time.perf_counter() - t0
        print(
            f"    Tier3 select_skills: {dt3:.2f} s  needs_skill={sel.needs_skill}  "
            f"skills={[s.skill_name for s in sel.skills]}"
        )
        _RESULTS["tier3_s"] = _round3(dt3)
    except Exception as e:  # noqa: BLE001
        print(f"    Tier3 select_skills 失败: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------
    # 汇总输出
    # ---------------------------------------------------------------
    print("\n===== 汇总 =====")
    print(json.dumps(_RESULTS, ensure_ascii=False, indent=2))

    new_v = (clean_times or new_times)          # 干净入参优先作为"优化后执行"
    if clean_times:
        _RESULTS["new_path_exec_mean"] = _round3(new_v and statistics.mean(new_v))
    old_v_mean = (_RESULTS.get("old_path_exec") or {}).get("mean")
    clean_mean = (_RESULTS.get("new_path_clean_arg") or {}).get("mean")
    raw_mean = (_RESULTS.get("new_path_raw") or {}).get("mean")
    if clean_mean and old_v_mean:
        reduction = (1 - clean_mean / old_v_mean) * 100
        _RESULTS["latency_reduction_pct"] = _round3(reduction)
        print(
            f"执行阶段(干净入参)均值: 旧 {old_v_mean}s -> 新 {clean_mean}s  "
            f"=>  降幅 {reduction:.1f}%"
        )
    elif old_v_mean and raw_mean:
        reduction = (1 - raw_mean / old_v_mean) * 100
        _RESULTS["latency_reduction_pct"] = _round3(reduction)
        print(
            f"执行阶段(整句入参)均值: 旧 {old_v_mean}s -> 新 {raw_mean}s  "
            f"=>  降幅 {reduction:.1f}%"
        )
    else:
        print("执行阶段降幅无法计算（缺任一均值）")
        _RESULTS["latency_reduction_pct"] = None

    _write_report(_RESULTS, old_times, old_iters)


def _write_report(results: dict, old_times: list, old_iters: list) -> None:
    """把结果落盘为 benchmark_latency_results.md（供简历面试引用）"""
    n_err = sum(1 for t in old_times if t is None)
    old_m = (results.get("old_path_exec") or {}).get("mean")
    raw_m = (results.get("new_path_raw") or {}).get("mean")
    clean_m = (results.get("new_path_clean_arg") or {}).get("mean")
    red = results.get("latency_reduction_pct")
    tier1 = results.get("tier1_ms")
    tier3 = results.get("tier3_s")
    iters = ", ".join(str(i) for i in old_iters if i is not None)
    iters = iters or "无"

    lines = [
        "# 技能调度器延迟实测结果",
        "",
        "> 复测设计文档估算（skills/skills_optimize_srh/skills_optimize_srh.md:16,163-172）：",
        "> `LLM 工具循环 12-55s -> subprocess 直跑 0.5-2s`",
        "",
        f"- 被测技能：`{SKILL_NAME}`（输入：`{HUMAN_INPUT}`；干净入参：`{CITY_ONLY}`）",
        "- 运行命令：`.venv/Scripts/python benchmark_skill_latency.py`",
        f"- 新路径样本 N={ROUNDS_NEW}（整句 {results.get('new_path_raw_ok','-')} 成功；",
        f"干净入参 {results.get('new_path_clean_ok','-')} 成功）；旧路径样本 N={ROUNDS_OLD}"
        f"（成功 {ROUNDS_OLD - n_err} / 失败 {n_err}，每轮 LLM 迭代 {iters}）",
        "",
        "## 汇总（实测）",
        "",
        "| 指标 | 优化前(LLM工具循环) | 优化后(subprocess 直跑) | 说明 |",
        "|---|---|---|---|",
        f"| 执行阶段延迟 mean | {old_m if old_m else '未测得'}s "
        f"| 干净入参 {clean_m if clean_m else 'N/A'}s / 整句 {raw_m if raw_m else 'N/A'}s "
        "| 干净入参代表执行机制本身 |",
        (f"| 执行阶段降幅 | N/A | **{red}%** | 1 - mean(new_clean)/mean(old) |"
         if red is not None else "| 执行阶段降幅 | N/A | N/A | 缺实测 |"),
        (f"| 技能选择 Tier1 关键词 | N/A | {tier1}ms | 命中后不走 LLM |"
         if tier1 is not None else ""),
        (f"| 技能选择 Tier3 LLM 结构化 | {tier3}s | N/A | 降级兜底 |"
         if tier3 is not None else ""),
        "",
        "> **注意（诚实披露）**：新路径直跑不做参数提炼，整句入参传给脚本会失败"
        "（weather-cn.sh 只接受城市名，如 `西安`）。",
        "> 干净入参才能代表执行机制本身的耗时；整句入参场景说明直跑路径需要一个前置参数抽取步骤。",
        "",
        "## 原始记录",
        "",
        "```json",
        json.dumps(results, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    try:
        with open("benchmark_latency_results.md", "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("  [ok] 已写入 benchmark_latency_results.md")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 写文件失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())