# Subagent-Driven Development Progress Ledger

**Plan:** docs/superpowers/plans/2026-07-10-backend-foundation.md
**Branch:** feature/backend-foundation
**Note:** Previous ledger content was stale (referenced unrelated old-script commits cdf92b9..895d046 which are pre-existing history, not SDD task outputs; webapp/backend did not exist). Cleared on 2026-07-10.

**ENVIRONMENT CONSTRAINT (discovered in Task 2):** Python 3.9.6 is the runtime. PEP 604 union syntax `X | None` does NOT work at runtime in 3.9 -- SQLAlchemy 2.0 `Mapped[X | None]` fails even with `from __future__ import annotations` because Mapped resolves types at runtime. Use `Optional[X]` from `typing` for all nullable type annotations in ORM models and function signatures. `list[float]` / `tuple[float, float]` (PEP 585) are OK in 3.9. Use `python3`/`pip3` not `python`/`pip`.

- Task 1: complete (commits ea047d3..7cdb78b, review clean)
- Task 2: complete (commits 7cdb78b..9a7570d, review clean)
- Task 3: complete (commits 9a7570d..3afbb39, review clean)
- Task 4: complete (commits 3afbb39..578f51c, review clean)
- Task 5: complete (commits 578f51c..d13f3e3, review clean)
- Task 6: complete (commits d13f3e3..20a9f13, review clean)
- Task 7: complete (commits 20a9f13..da65936, review clean)
- Task 8: complete (commits da65936..4240ed1, review clean)
- Task 9: complete (commits 4240ed1..a0ed534, review clean)
- Task 10: complete (commits a0ed534..c1a4349, review clean)

**ALL 10 TASKS COMPLETE. Proceeding to final whole-branch review.**

**Final whole-branch review: PASSED (Ready to merge: Yes)**
- 3 Important findings fixed (commit 5386e0a): dead Geltner test, updated_at onupdate, month-gap detection (CLAUDE.md 第一条).
- Systematic server_default timestamp bug fixed (commits fea0d7f, 39eef4a): all 4 timestamp columns now use text("(datetime('now'))").
- Re-review (opus): all 5 fixes verified correct, 43/43 tests passing, no new issues.

## Minor findings (defer to final whole-branch review)
- Task 4: `calculate_max_consecutive_underperform` 测试缺少 r==0 用例钉住"含持平"语义（docstring 说含 0，代码 `r<=0` 正确，但无 0 值测试断言）。建议补 `calculate_max_consecutive_underperform([0.0, 0.0, 0.01]) == 2`。
- Task 4: `calculate_excess_win_rate` 全负数用例未测（风险低）。
- Task 7: `test_compute_all_metrics_with_geltner` 中 `is_q_significant in (0,1)` 为弱断言（q_stat=60.09 显著，可强化为 `==1`）。
- Task 7: `_annualized_excess_return_compounded` 的 n<=0 守卫返回 0.0，与 `calculate_annualized_return` 对 n=0 抛 ValueError 不一致（调用链中 n=0 不触发，非阻塞）。
