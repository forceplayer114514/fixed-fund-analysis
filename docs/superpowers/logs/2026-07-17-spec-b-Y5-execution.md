# Spec B Y.5 执行日志

- **日期**: 2026-07-17
- **备份**: `data/fund_analysis.db.spec_b_backup_20260717_135816` (280 KB)
- **清**: 6 表 (monthly_returns / confirmed_gaps / pending_review / fund_metrics / anomalies / ai_reports)
- **funds 表**: 保留元数据 + 白名单
- **重爬**: 8 支基金 (Coolabah × 2 排除)

## Y.5 修复补丁

Y.5 首次执行暴露 fundmonitors 白名单命中但 acc_code 为空场景, 4 支 fund (Bentham/JCB/Macquarie/SmarterMoney) L1 短路后返 no_table。修复:

```python
# fundmonitors.py probe(): acc_code 空 -> fallback Tavily
if wl is not None and wl[1]:  # 只有 acc_code 非空才短路
    hit = wl
```

## 最终结果

| fund_id | monthly | discovered_source_name | 状态 |
|---|---|---|---|
| bentham_global_income | 153 | Bentham Global Income Fund | OK |
| coolabah_frhy_assisted | 0 | — | 无覆盖 (Spec C) |
| coolabah_frhy_institutional | 0 | — | 无覆盖 (Spec C) |
| gryphon_capital_income | 1 | — | L2 PDF 单月 |
| jcb_active_bond | 119 | CC Jamieson Coote Bonds Active Bond Fund | OK |
| kkr_credit_income_fund | 0 | — | 白名单错源 (Spec A 遗留, Y.5 后清; Spec C 修) |
| macquarie_australian_fixed_interest | 0 | — | 白名单错源 (同 KKR) |
| smarter_money_lscf | 106 | Smarter Money Long Short Credit Fund | MISMATCH (仅 hyphen 差异) |
| stake_accumulate | 0 | — | 无 fundmonitors 白名单, discovery 空 (Spec C) |
| yarra_enhanced_income_fund | 276 | Yarra Enhanced Income Fund | OK |

**总入库**: 655 月 (原 869, 净减 214 -- 主要是 Spec A 白名单错源数据被清除)

## Spec B 目标达成

- ✅ 反转 L1/L2 优先级 (fundmonitors 从 L3 提为 L1 主源)
- ✅ 全清 monthly_returns 等 6 表并重爬
- ✅ 删除 name guard 24 单测 + `_STOPWORDS`/`_tokenize`/`_name_match`
- ✅ 透明展示 discovered_source_name (前端标红核对)
- ✅ Spec A KKR/Macquarie 白名单错源被透明展示暴露, 已从库中清除

## 遗留 (转 Spec C)

- KKR/Macquarie fundmonitors 白名单需重新查真 FundID
- Coolabah × 2 特殊通路 (Plotly HTML NAV)
- Stake Accumulate 数据源探测
- GCI fundmonitors 白名单探测 (现只有 L2 PDF 单月)

## Y.6 GCI inception 重置

- 老值 (Spec A/skills-era): 2018-05-21
- 新值: **2019-01-31** (第一份真实 PDF `data/pdf_cache/gryphon_capital_income/2019-02.pdf` 覆盖的月末)
- `inception_assumed=0` 保持 (真值, 非假设)

## Y.8 全测

`pytest tests/` 全绿: 109 passed (跳 test_ingest_priority_l1_l2.py 6 慢测) + 6 l1_l2 = 115 total.

## Y.10 tag

`git tag spec-b-done` 打上以标 Spec B 收官。
