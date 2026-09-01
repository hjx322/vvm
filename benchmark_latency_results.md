# 技能调度器延迟实测结果

> 复测设计文档估算（skills/skills_optimize_srh/skills_optimize_srh.md:16,163-172）：
> `LLM 工具循环 12-55s -> subprocess 直跑 0.5-2s`

- 被测技能：`clawhub_weather-cn`（输入：`西安的天气怎么样`；干净入参：`西安`）
- 运行命令：`.venv/Scripts/python benchmark_skill_latency.py`
- 新路径样本 N=5（整句 0/5 成功；
干净入参 5/5 成功）；旧路径样本 N=3（成功 3 / 失败 0，每轮 LLM 迭代 2, 2, 2）

## 汇总（实测）

| 指标 | 优化前(LLM工具循环) | 优化后(subprocess 直跑) | 说明 |
|---|---|---|---|
| 执行阶段延迟 mean | 4.97s | 干净入参 1.085s / 整句 0.201s | 干净入参代表执行机制本身 |
| 执行阶段降幅 | N/A | **78.169%** | 1 - mean(new_clean)/mean(old) |
| 技能选择 Tier1 关键词 | N/A | 0.068ms | 命中后不走 LLM |
| 技能选择 Tier3 LLM 结构化 | 1.817s | N/A | 降级兜底 |

> **注意（诚实披露）**：新路径直跑不做参数提炼，整句入参传给脚本会失败（weather-cn.sh 只接受城市名，如 `西安`）。
> 干净入参才能代表执行机制本身的耗时；整句入参场景说明直跑路径需要一个前置参数抽取步骤。

## 原始记录

```json
{
  "new_path_raw": {
    "n": 5,
    "mean": 0.201,
    "median": 0.198,
    "min": 0.18,
    "max": 0.217
  },
  "new_path_raw_ok": "0/5",
  "new_path_clean_arg": {
    "n": 5,
    "mean": 1.085,
    "median": 1.095,
    "min": 1.021,
    "max": 1.14
  },
  "new_path_clean_ok": "5/5",
  "old_path_exec": {
    "n": 3,
    "mean": 4.97,
    "median": 5.498,
    "min": 3.756,
    "max": 5.655
  },
  "tier1_ms": 0.068,
  "tier3_s": 1.817,
  "new_path_exec_mean": 1.085,
  "latency_reduction_pct": 78.169
}
```
