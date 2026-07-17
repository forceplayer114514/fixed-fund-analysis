# Task 5 Report: 自相关、Geltner 去平滑三重防火墙

## What I Implemented

Appended to `webapp/backend/app/calculations.py` (existing 6 functions unchanged, append-only):
- `calculate_autocorrelation(returns, fund_name="Unknown") -> tuple[float, float]`: 一阶自相关系数 phi 与 Ljung-Box Q 统计量。n<2 或 denominator==0 返回 (0.0, 0.0)；`q_stat = n*(n+2)*phi^2/(n-1)`。
- `unsmooth_returns(returns, phi, fund_name="Unknown") -> list[float]`: Geltner 去平滑 `r'_t = (r_t - phi*r_{t-1})/(1-phi)`；空列表返回 []；首元素原样保留；`1-phi < 0.01` 抛 ValueError。
- `LJUNG_BOX_CRITICAL_VALUE = 3.841`: 模块级常量（Ljung-Box 95% 置信度，自由度1），可供 Task 7 的 `compute_all_metrics` 导入。
- `should_apply_geltner(n_months, phi, q_stat) -> bool`: 三重防火墙判定，三者全满足（AND 语义）才返回 True：
  1. `n_months >= 36`
  2. `q_stat > 3.841`
  3. `0 <= phi <= 0.85`

Appended to `webapp/backend/tests/test_calculations.py`:
- `test_calculate_autocorrelation`: 平滑序列 phi>0.9 + q_stat>0；短序列返回 (0.0, 0.0)。
- `test_unsmooth_returns`: 数值正确性（phi=0.5）；phi=0.999 抛 ValueError 含 "phi=0.999"；空列表返回 []。
- `test_should_apply_geltner_three_firewalls`: 全通过为 True；分别验证防火墙1（n<36）、防火墙2（Q<=3.841）、防火墙3（phi<0、phi>0.85）各返回 False。

## TDD Evidence

**RED**: 追加 3 个测试后运行 `python3 -m pytest tests/test_calculations.py -v -k "autocorrelation or unsmooth or geltner"`，结果 `ImportError: cannot import name 'calculate_autocorrelation' from 'app.calculations'`（1 error during collection），符合预期。

**GREEN**: 追加实现后运行 `python3 -m pytest tests/test_calculations.py -v`，结果 `9 passed, 9 warnings`（warnings 为无害的 `@pytest.mark.unit` 未注册标记告警）。完整后端测试套件 `16 passed`。

## Numerical Parity Verification

`calculate_autocorrelation` 与 `unsmooth_returns` 从 `scripts/metrics.py` 逐字移植。已用 Python 脚本交叉验证：对新实现与 `scripts.metrics.calculate_autocorrelation` 喂入相同输入，输出完全一致（repeated ramp: phi=0.040000/q=0.084898；pure ramp: phi=0.940000/q=46.884898，两者逐位相同）。

## Files Changed

- `webapp/backend/app/calculations.py`（+52 行：3 函数 + 1 常量，纯追加）
- `webapp/backend/tests/test_calculations.py`（+36 行：3 导入 + 3 测试，纯追加）

## Self-Review Findings

- **Completeness**: 常量 + 3 函数均已追加；3 个新测试通过；原有 6 个测试仍通过。OK
- **Quality**: 全为纯函数（无 DB/网络 IO）；三重防火墙采用 AND 语义（任一不满足即 False，全满足才 True）；中文注释齐全。OK
- **Discipline**: diff 确认仅追加，原有 6 个函数与 6 个测试零改动。OK
- **Testing**: 平滑序列 phi=0.94>0.9 OK；phi=0.999 抛 ValueError 且消息含 "phi=0.999" OK；`should_apply_geltner` 覆盖全部 3 道防火墙（n<36、Q<=3.841、phi<0、phi>0.85）OK；测试输出干净（仅无害 mark 告警）OK。

## Concerns

**1. Brief 测试数据与 verbatim 实现内部冲突（已修正测试数据）：**
Brief Step 1 的测试数据 `[0.01, 0.02, 0.03, 0.04, 0.05] * 10` 配合 Brief Step 3 的 verbatim 实现（数值与 `scripts/metrics.py` 完全一致）实际产出 phi=0.04，并不满足断言 `phi > 0.9`。根因：`* 10` 把 5 点斜坡重复 10 次，每次 0.05->0.01 的回绕跳变摧毁了自相关。

任务明确要求 `calculate_autocorrelation` 与 `metrics.py` 数值一致（REQUIRED），且 self-review 要求 "autocorrelation phi>0.9 for smooth series"。两者均为硬性要求，唯一可调整的是 Brief 中有缺陷的测试数据。故将测试数据替换为真正的 50 点单调平滑序列 `[0.01 * i for i in range(1, 51)]`（phi=0.94>0.9，q_stat=46.88>0），实现与断言均保持不变，符合测试注释 "完全正自相关序列：phi 接近 1" 的本意。

这是一处对 Brief 字面测试数据的偏离，已在测试注释中标注原因。如需严格回退到 Brief 原数据，则必须放宽断言（如 `phi > 0`），但这会违背 self-review 明确要求的 phi>0.9，故未采纳。

**2. 提交信息：** 按 Brief Step 5 原文使用 `Co-Authored-By: Claude Fable 5`（与本计划前序 Task 3-4 提交风格一致）。

**3. 未提交的无关改动：** 工作区存在 `.superpowers/sdd/` 下 progress/brief/report 文件的未暂存修改（任务开始前即存在），不属于本任务范围，未纳入提交。
