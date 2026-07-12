# Skills PDF 提取优化 SDD Progress Ledger

优化目标：单基金入库 27' -> 3-5'（修 bug + 固化 + 硬 gate + 并发）
计划文件：`skills/.claude/plans/optimization_plan.md`

## 阶段进度
- 阶段0 环境清理: complete（/tmp 9 脚本清除，inspect 遮蔽解除）

## Task 进度
- Task 1: complete (commit ac3031c, 27 passed, review clean)
  - extract.py 4 提取函数（extract_commentary_return / extract_perf_rolling / extract_pdf_one / extract_pdf_links_from_archive）+ 16 测试
  - 偏差1: _pct_to_decimal 用 Decimal 精确十进制映射（避免 float/100 二进制舍入，非合理性纠正）
  - 偏差2: extract_perf_rolling 搜索范围 lines[header_idx:] + Class A 后切片（处理表头数据行合并，更鲁棒）
  - review: 2 偏差均合理，数据完整性约束全遵守
- Task 2: complete (commit 82b1852, 48 passed, review clean)
  - extract.py 3 函数（download_and_extract_parallel / verify_monthly_vs_rolling / gate_check）+ 10 测试
  - 无偏差，逐字按 brief
- Task 3: complete (commit e6aeb45, 52 passed, review clean)
  - lib/ingest.py（add_fund 全自动流水线 + CLI）+ test_ingest.py 4 用例
  - 偏差: brief 测试数据 test2/test4 URL 缺年份致 extract_pdf_links 返回 []，改 URL 加年份（实现零改动，合理）
- Task 4: complete (commit aef9916, 2 文档改动, review clean)
  - add_fixed_fund.md 工作流改用 python3 -m lib.ingest add + 硬约束 7 条
  - update_fixed_fund.md 补硬约束 7 条 + 提及 extract_commentary_return/gate_check
  - 偏差: NAV 表述更准确（ingest 返回 dict 不含 NAV）/ update 步骤编号修正（均合理）

## 全部完成
4 task 全 complete，52 passed 无回归。
- commit: ac3031c (Task1) -> 82b1852 (Task2) -> e6aeb45 (Task3) -> aef9916 (Task4)
- 新增: extract.py 7 函数（4 提取 + 3 并发校验）、lib/ingest.py 全自动流水线 + CLI、两文档硬约束
- 预期: 单基金入库 27' -> 3-5'（瓶颈从 agent 试错回合变成网络下载，可并发压缩）

## 端到端验收（完成）
- 委派子 agent stealthy_fetch 抓 Stake 归档页（15 PDF 链接，2025-03~2026-05）
- 跑 `python3 -m lib.ingest add --fund-id stake_accumulate_test`：gate_pass=True，15 月入库，**耗时 21 秒**
- 数据验证：net_return 与已入库 stake_accumulate **0 不一致**，末月 NAV=1.069128 完全一致
- 验收中发现并修复 extract_pdf_links_from_archive bug（commit b700721）：URL 2 位年份/hash 误匹配，改链接文本优先
- 清理测试数据 stake_accumulate_test + /tmp 产物，原 stake_accumulate 未受影响

## 优化效果
- 单基金入库：**27'35" -> 21 秒**（ingest.py 纯程序，含 15 PDF 并发下载+提取+gate+入库）
- 提速 ~78 倍（纯程序）/ ~14 倍（含抓归档页 MCP 子 agent）
- 瓶颈从 agent 试错回合变成网络下载（已并发压缩）

## 未做（计划阶段4，可选）
- LLM 兜底文档：本次仅留接口，未写文档节
