# 探针：验证 Tier3「LLM 判定无需技能」不再被兜底成「跑全池」（离线，无需服务/DB/真实 LLM/网络）
# 用法：.venv\Scripts\python.exe scripts\probe_tier3_no_skill.py
#
# 背景：select_skills_in_pool 原先只有两种结局（命中 / 回退全量池），因为 select_skills
#       在 LLM 调用失败时也返回 needs_skill=False 的对象，与"LLM 明确判定无需技能"不可区分。
#       后果：search_agent 被派"制定健身计划"，池里无对口技能，却仍把 [web_search,
#       clawhub_weather-cn] 全跑一遍（白花一次联网调用 + 无关内容污染综合语料）。
#
# 断言"结局1"是本探针的核心：LLM 明确说"不需要技能"时，必须一个技能都不执行。
# 断言"结局2"是它的反向守卫：LLM 调用失败时仍要保守回退全池，不能连带把功能丢掉。
import asyncio
import sys

sys.path.insert(0, r"f:\1Github\vm")

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")  # 静音 INFO，保留 WARNING/ERROR

from skills.skills_optimize_srh.skill_dispatcher import (  # noqa: E402
    SkillCallItem,
    SkillSelectionResult,
    UnifiedSkillDispatcher,
)

# 一个 Tier1/2 必定落空的 query × 池组合（关键词与 FTS5 都抓不到），保证必然走到 Tier3
QUERY = "我想制定健身计划"
POOL = ["web_search", "clawhub_weather-cn"]  # 池内无一对口该问题
USER_ID = ""  # 空租户：避开自定义技能加载，结果与 DB 无关

_failed: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        _failed.append(name)


def main() -> None:
    disp = UnifiedSkillDispatcher(None)
    state = {"user_id": USER_ID, "doctor_id": ""}

    # ---- 前置：确认这一组合确实绕过 Tier1/2（否则本探针根本没测到 Tier3）----
    t12 = disp.registry.match(QUERY, USER_ID, pool=POOL)
    print(f"\n[前置] Tier1/2 命中={t12}（必须为空，否则测不到 Tier3）")
    if t12:
        print("[SKIP] 该组合被 Tier1/2 拦下，探针前提不成立")
        return

    # ================= 结局 1：LLM 判定「无需技能」→ 一个都不执行 =================
    async def _no_skill(q, s, pool=None, agent_prompt=""):
        return SkillSelectionResult(needs_skill=False, skills=[], reply="直接回答即可")

    disp.select_skills = _no_skill  # type: ignore[assignment]
    out = asyncio.run(disp.select_skills_in_pool(POOL, QUERY, state))
    print(f"\n[结局1] LLM 判定无需技能 → select_skills_in_pool = {out}")
    check("结局1: LLM 判无需技能时返回空（不跑全池）", out == [], f"实际={out}")
    check(
        "结局1: 绝不等同于回退全量池",
        sorted(out) != sorted(POOL),
        f"pool={POOL}",
    )

    # ================= 结局 2：LLM 调用失败（None）→ 保守回退全量池 =================
    async def _llm_fail(q, s, pool=None, agent_prompt=""):
        return None

    disp.select_skills = _llm_fail  # type: ignore[assignment]
    out = asyncio.run(disp.select_skills_in_pool(POOL, QUERY, state))
    print(f"\n[结局2] LLM 调用失败 → select_skills_in_pool = {out}")
    check("结局2: LLM 失败时保守回退全量池（不丢功能）", out == POOL, f"实际={out}")
    check("结局2: 与结局1 结果必须不同", out != [], "两者不可混同")

    # ================= 结局 3：LLM 说需要技能但给不出池内名字 → 保守回退全池 =========
    async def _hallucinated(q, s, pool=None, agent_prompt=""):
        return SkillSelectionResult(
            needs_skill=True,
            skills=[SkillCallItem(skill_name="no_such_skill", reason="幻觉")],
        )

    disp.select_skills = _hallucinated  # type: ignore[assignment]
    out = asyncio.run(disp.select_skills_in_pool(POOL, QUERY, state))
    print(f"\n[结局3] LLM 输出池外技能名 → select_skills_in_pool = {out}")
    check("结局3: 名字全在池外时保守回退全池", out == POOL, f"实际={out}")

    # ================= 结局 4：LLM 正常命中池内技能（原行为不得退化）===============
    async def _hit(q, s, pool=None, agent_prompt=""):
        return SkillSelectionResult(
            needs_skill=True,
            skills=[SkillCallItem(skill_name="web_search", reason="联网检索")],
        )

    disp.select_skills = _hit  # type: ignore[assignment]
    out = asyncio.run(disp.select_skills_in_pool(POOL, QUERY, state))
    print(f"\n[结局4] LLM 池内命中 → select_skills_in_pool = {out}")
    check("结局4: 正常命中池内技能（行为不变）", out == ["web_search"], f"实际={out}")

    # ================= 结局 5：SkillExecutor 正确把「跳过」与「失败」分开 ===========
    from agent.multi_agent.skill_executor import SkillExecutor

    ex = SkillExecutor(disp)
    disp.select_skills = _no_skill  # type: ignore[assignment]
    r = asyncio.run(ex.select_and_execute(POOL, QUERY, state, agent_prompt="测试"))
    print(f"\n[结局5] SkillExecutor 返回值 = {r}")
    check(
        "结局5: 跳过时 skipped=True 且 successful/failed 均空",
        r.get("skipped") is True and not r["successful"] and not r["failed"],
        f"实际={r}",
    )

    # ================= 结局 6：BaseAgent 把 skipped 透传进 AgentResult ==============
    from agent.multi_agent.schemas import AgentTask
    from agent.multi_agent.workers import SearchAgent

    class _FakeProvider:
        """假 provider：把有效池撑到 ≥2 个技能，否则 len(pool)<=1 会短路掉 Tier3
        （SearchAgent 类属性只有 ['web_search']，不挂 provider 根本走不到 Tier3）"""

        async def get_enabled_skill_names(self, agent_name):
            return list(POOL)

    agent = SearchAgent(llm=None, dispatcher=disp, skill_provider=_FakeProvider())
    res = asyncio.run(
        agent.run(AgentTask(name="search_agent", human_input=QUERY), dict(state))
    )
    print(f"\n[结局6] SearchAgent AgentResult: success={res.success} skipped={res.skipped}")
    check("结局6: 跳过被标记为 skipped=True（非执行失败）", res.skipped is True)
    check("结局6: 无技能执行时 success 应为 False（不谎报成功）", res.success is False)
    check(
        "结局6: content 不得是『未查询到结果』占位串混进综合语料",
        res.content != "未查询到结果",
        f"实际={res.content!r}",
    )

    # ================= 不变量：Tier1/2 命中时不得走到 Tier3 ========================
    async def _boom(q, s, pool=None, agent_prompt=""):
        raise AssertionError("Tier1/2 已命中却仍调用了 Tier3！")

    disp.select_skills = _boom  # type: ignore[assignment]
    out = asyncio.run(
        disp.select_skills_in_pool(["clawhub_weather-cn"], "什么是天气", state)
    )
    print(f"\n[不变量] Tier1/2 命中 → {out}（未触发 Tier3）")
    check("不变量: Tier1/2 命中时短路 Tier3", out == ["clawhub_weather-cn"], f"实际={out}")

    # ================= 不变量：空池 / 单技能池仍直接返回 ===========================
    out_empty = asyncio.run(disp.select_skills_in_pool([], QUERY, state))
    out_one = asyncio.run(disp.select_skills_in_pool(["web_search"], QUERY, state))
    print(f"\n[不变量] 空池={out_empty}  单技能池={out_one}")
    check("不变量: 空池直接返回空", out_empty == [])
    check("不变量: 单技能池直接返回该技能", out_one == ["web_search"])

    print("\n" + "=" * 60)
    if _failed:
        print(f"[FAIL] {len(_failed)} 条断言失败：")
        for n in _failed:
            print("   -", n)
        sys.exit(1)
    print("[PASS] 全部断言通过")


if __name__ == "__main__":
    main()
