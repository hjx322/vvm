# 探针：验证技能匹配的"池作用域"是否生效（离线，无需服务/DB/LLM/网络）
# 用法：.venv\Scripts\python.exe scripts\probe_skill_pool_scope.py
#
# 背景：DynamicSkillRegistry.match 原为全库匹配，调用方再与子 agent 技能池取交集。
#       池外技能的 Tier1 命中会误触发 `if not hits` 短路，使 Tier2(FTS5) 被跳过，
#       池内技能失去召回机会。本次改动把作用域下沉到 match 内部。
# 断言 6 是本探针的核心：它钉死"过滤必须在 top-N 截断之前"这一正确性要求。
import sys

sys.path.insert(0, r"f:\1Github\vm")

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")  # 静音 INFO，保留 WARNING/ERROR

from skills.skills_optimize_srh.skill_dispatcher import UnifiedSkillDispatcher

USER_ID = "1827196"
WEATHER = "什么是天气"
RECORD = "帮我看看就诊记录"

# 改前基线：全库匹配结果（pool=None 语义，本次改动不得影响）
FULL_BASELINE = {
    "什么是天气": ["milvus_query", "clawhub_weather-cn"],
    "帮我看看就诊记录": ["mysql_query"],
    "最近有什么新闻": ["web_search"],
    "你好": [],
    "什么是高血压": ["milvus_query"],
    "制定健身计划": ["healthfit"],
    "我皮肤上有个痣": ["derma_image"],
}

# 改动前为空、改动后应命中的池场景（池外 Tier1 命中曾误短路 Tier2）
POOL_RECOVERY_CASES = [
    (WEATHER, ["derma_image"]),
    (RECORD, ["derma_image"]),
    ("什么是高血压", ["web_search"]),
]

_failed = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  <- {detail}" if detail and not cond else ""))
    if not cond:
        _failed.append(name)


def main():
    disp = UnifiedSkillDispatcher(None)       # llm=None → 纯离线
    disp._ensure_user_skills_loaded(USER_ID)  # 只读 user_skills/ 目录
    reg = disp.registry

    print("== 1. 全库语义冻结（pool=None 必须与改动前逐元素一致）==")
    for q, expected in FULL_BASELINE.items():
        got = reg.match(q, USER_ID)
        got_none = reg.match(q, USER_ID, pool=None)
        check(
            f"{q!r} -> {got}",
            got == expected == got_none,
            f"期望 {expected} / pool=None 得 {got_none}",
        )

    print("\n== 2. 空作用域不退化（防 `if pool:` 真值判断回归）==")
    empty = reg.match(WEATHER, USER_ID, pool=[])
    check("pool=[] 返回 []", empty == [], f"实际 {empty}（退化成全库 = 作用域越权）")

    print("\n== 3. 作用域不变量：任意 query 的结果必须 ⊆ pool ==")
    pool = ["web_search", "clawhub_weather-cn"]
    violations = [
        q for q in FULL_BASELINE
        if not set(reg.match(q, USER_ID, pool=pool)) <= set(pool)
    ]
    check(f"pool={pool} 无越界", not violations, f"越界 query: {violations}")

    print("\n== 4. 池外 Tier1 命中不再短路 Tier2（改动前这些全为 []）==")
    for q, p in POOL_RECOVERY_CASES:
        got = reg.match(q, USER_ID, pool=p)
        check(f"{q!r} pool={p} -> {got}", bool(got), "改动前为空（Tier2 被误短路）")

    print("\n== 5. 池内 Tier1 命中优先于 Tier2 ==")
    got = reg.match(WEATHER, USER_ID, pool=["clawhub_weather-cn"])
    check("命中 clawhub_weather-cn", got == ["clawhub_weather-cn"], f"实际 {got}")

    print("\n== 6. ★ 核心：FTS 过滤必须在 top-N 截断之前 ==")
    # 前提自证：不带 pool 时 derma_image 落在 FTS top-3 之外（已被截断）
    top3 = reg._fts_search(WEATHER, USER_ID)
    check(f"前提成立：derma_image 不在 FTS top-3 内 (top3={top3})",
          "derma_image" not in top3)
    # 若有人把过滤挪到 ranked[:_FTS_MAX_RESULTS] 之后，这条会退化成 []，立即报红
    scoped = reg._fts_search(WEATHER, USER_ID, pool={"derma_image"})
    check(f"截断前过滤可召回池内技能 -> {scoped}",
          scoped == ["derma_image"],
          "过滤若放在截断之后，top-3 已被池外技能占满，此处会得到 []")
    got = reg.match(WEATHER, USER_ID, pool=["derma_image"])
    check(f"经 match 的同一场景 -> {got}", got == ["derma_image"])

    print("\n== 7. 边界：池含未注册技能名 ==")
    got = reg.match(WEATHER, USER_ID, pool=["no_such_skill"])
    check("未注册技能不会凭空命中", got == [], f"实际 {got}")

    print("\n" + "=" * 60)
    if _failed:
        print(f"[FAIL] {len(_failed)} 条断言失败：")
        for n in _failed:
            print("   -", n)
        sys.exit(1)
    print("[PASS] 全部断言通过")


if __name__ == "__main__":
    main()
