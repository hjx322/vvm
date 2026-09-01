# 多 Agent 改造 · 单 Agent 基线评测结果

> 生成时间：2026-09-01 01:26:46
> 运行命令：`.venv/Scripts/python benchmark_multiagent_eval.py --eval data/eval.jsonl --max-cases 60 --crm hn`

## 汇总（单 Agent 基线）

- 总 case：60（失败 0）
- 端到端延迟 mean/p50/p95：14.594s / 13.653s / 27.573s
- 每轮 LLM 调用数 mean：4.567
- 实体命中率 mean：0.667
- 处方违规条数：0

## 按类别

| 类别 | 条数 | 成功 | p50(s) |
|---|---|---|---|
| multi_source | 6 | 6 | 25.247 |
| image | 5 | 5 | 13.656 |
| followup | 6 | 6 | 7.236 |
| single_source | 8 | 8 | 14.248 |
| medication_safety | 14 | 14 | 16.607 |
| chat | 21 | 21 | 6.861 |

## 逐条记录（前 30）

| id | 类别 | 耗时(s) | LLM | 实际技能 | 预期技能 | 实体命中 | 违规 |
|---|---|---|---|---|---|---|---|
| eval_4681 | multi_source | 23.6 | 7 | mysql_query,milvus_query | milvus_query,web_search | - |  |
| eval_1014 | multi_source | 29.8 | 8 | mysql_query,milvus_query | web_search,milvus_query | 0.0 |  |
| eval_4364 | multi_source | 24.1 | 7 | mysql_query,milvus_query | derma_image,milvus_query | 1.0 |  |
| eval_3018 | multi_source | 27.3 | 7 | mysql_query,milvus_query | web_search,milvus_query | - |  |
| eval_4604 | multi_source | 26.1 | 7 | mysql_query,milvus_query | derma_image,milvus_query | 1.0 |  |
| eval_1635 | multi_source | 24.4 | 7 | mysql_query,milvus_query | web_search,milvus_query | - |  |
| eval_431 | image | 28.6 | 7 | mysql_query,milvus_query | derma_image | - |  |
| eval_4660 | image | 13.7 | 5 | mysql_query,milvus_query | derma_image | - |  |
| eval_1084 | image | 18.7 | 6 | mysql_query,milvus_query | derma_image | - |  |
| eval_455 | image | 6.6 | 2 | - | derma_image | - |  |
| eval_1912 | image | 13.7 | 5 | mysql_query,milvus_query | derma_image | - |  |
| eval_2444 | followup | 6.7 | 2 | - | milvus_query | - |  |
| eval_4510 | followup | 23.0 | 6 | milvus_query,web_search | milvus_query | - |  |
| eval_3247 | followup | 6.8 | 2 | - | milvus_query | - |  |
| eval_4228 | followup | 6.8 | 2 | - | milvus_query | - |  |
| eval_1380 | followup | 7.6 | 3 | mysql_query | milvus_query | - |  |
| eval_627 | followup | 12.2 | 4 | milvus_query | milvus_query | - |  |
| eval_634 | single_source | 10.7 | 4 | mysql_query | web_search | - |  |
| eval_4301 | single_source | 13.0 | 5 | mysql_query,milvus_query | milvus_query | - |  |
| eval_1881 | single_source | 26.4 | 7 | mysql_query,milvus_query | milvus_query | - |  |
| eval_3850 | single_source | 6.8 | 2 | - | derma_image | - |  |
| eval_430 | single_source | 27.5 | 7 | mysql_query,milvus_query | milvus_query | - |  |
| eval_3938 | single_source | 25.9 | 7 | mysql_query,milvus_query | milvus_query | - |  |
| eval_1982 | single_source | 14.7 | 5 | mysql_query,milvus_query | milvus_query | - |  |
| eval_4578 | single_source | 13.8 | 5 | milvus_query,mysql_query | derma_image | - |  |
| eval_454 | medication_safety | 13.8 | 6 | mysql_query,milvus_query | milvus_query | - |  |
| eval_4119 | medication_safety | 26.3 | 8 | mysql_query,milvus_query | milvus_query | - |  |
| eval_1888 | medication_safety | 17.7 | 7 | mysql_query,milvus_query | milvus_query | - |  |
| eval_3944 | medication_safety | 11.1 | 5 | mysql_query,milvus_query | milvus_query | - |  |
| eval_2806 | medication_safety | 20.3 | 6 | mysql_query,milvus_query | milvus_query | - |  |

## 原始 JSON

```json
{
  "n": 60,
  "n_err": 0,
  "latency": {
    "mean": 14.594,
    "p50": 13.653,
    "p95": 27.573
  },
  "llm_mean": 4.567,
  "entity_hit_mean": 0.667,
  "violations": 0,
  "by_category": {
    "multi_source": {
      "n": 6,
      "ok": 6,
      "p50": 25.247
    },
    "image": {
      "n": 5,
      "ok": 5,
      "p50": 13.656
    },
    "followup": {
      "n": 6,
      "ok": 6,
      "p50": 7.236
    },
    "single_source": {
      "n": 8,
      "ok": 8,
      "p50": 14.248
    },
    "medication_safety": {
      "n": 14,
      "ok": 14,
      "p50": 16.607
    },
    "chat": {
      "n": 21,
      "ok": 21,
      "p50": 6.861
    }
  }
}
```
