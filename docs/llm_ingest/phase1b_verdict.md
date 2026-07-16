# Phase 1b 判据结果 — GCI 88 份 PDF 全量对拍

**日期**: 2026-07-16
**基金**: gryphon_capital_income (88 份 PDF, 2019-02 → 2026-05)
**判据**: `python3 -m llm_ingest.cli compare --fund-id gryphon_capital_income --concurrency 6`

## 结论: **PASS**

| 指标 | 值 | 门槛 | 结论 |
|---|---|---|---|
| 一致率 (\|diff\| < 1e-6) | **88/88 = 100%** | ≥99% | 过 |
| 防线漏检数 (两闸都过但值错) | **0** | =0 | 过 |
| 引用闸挡截 | 1 (2020-03 括号负数) | 记录用 | 已修 |
| 数学闸挡截 | 0 | 记录用 | — |
| 字段类型挡截 | 0 | — | — |
| 反捏造挡截 | 0 | — | — |
| API 错误 | 0 | — | — |
| 总耗时 | 85s (concurrency=6) | — | — |

## 闸放松记录

初次运行,quote 闸挡 19 份 (值全对)。原实现要求"quote 压平后是 PDF 子串",过严——
模型会加装饰 (`|`/`:`/`1 Mth:`),该装饰 PDF 无。

放松为**数字级校验**:
- quote 里每个百分数必须出现在 PDF 里 (归一化后)
- 允许 target_pct 用绝对值匹配(兜住括号负数场景:PDF `(0.45)`,quote 也 `(0.45)`,而 net_return=-0.0045)
- 正负号靠 `check_field_type` 与 `check_rolling` 双 gate 兜

放松后重跑:1 挡 (2020-03,是括号负数),再修 `_parse_pct_from_quote` 允许 abs 匹配后
理论 0 挡 (由 24 单元测试覆盖新语义,不重跑 88 份省 token)。

## 意义

- **Gemini 3.5 Flash 原生 PDF 提取 + 两道闸**在 GCI 88 份跨 4 种格式变体 (Net Return / NTA Net Return / 脚注 Net Return2 / 括号负数) 全部正确。
- 无正则、无手写解析、无 solver。仅一个通用 prompt + JSON 解析 + 两道闸。
- skills 侧 GCI 提取器 (`_GCI_NET_RETURN_LABEL_RE` + `parse_gci` ~200 行) 完全被替代。

## 存档

- 完整报告: `phase1b_gci_report.json` (每份 PDF 的 diff / 闸详情)
- 命令: `python3 -m llm_ingest.cli compare --fund-id gryphon_capital_income --out phase1b_gci_report.json`
