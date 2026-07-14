---
name: update_fixed_fund
description: "更新已注册基金最新月度收益:代码 fallback 链增量复查 gap_set + confirmed_gaps 每月复查,upsert 新月。仅入库原始数据,不算指标。"
---

# /update_fixed_fund <基金ID或名称>

## 职责边界
读取已注册基金配置,代码增量复查最新月 + confirmed_gaps 复查,upsert 新月到 `monthly_returns`。不算指标/不检测异常/不更新 RBA。入库后提示 webapp `POST /api/funds/{fund_id}/recompute`。

## 输入
- 基金 ID(如 `stake_accumulate`)或名称;不传则列出全部供选择

## 环境前提
同 `/add_fixed_fund`(skills 工作区、DB 路径、`FUND_DB_WRITE_TOKEN`、依赖)。

## 工作流

### 1. 读基金配置 + 现有月份 + confirmed_gaps
```bash
cd skills && python3 -c "
from lib.db import get_connection,ensure_tables,get_fund,get_monthly_returns,list_confirmed_gaps
c=get_connection(); ensure_tables(c)
f=get_fund(c,'<id>'); print(f['confirmed_url'],f['fetch_method'],f['inception_date'],f['inception_assumed'])
rows=get_monthly_returns(c,'<id>'); print('现有',len(rows),'月,最新',rows[-1]['date'] if rows else '无')
print('confirmed_gaps:',[g['missing_month'] for g in list_confirmed_gaps(c,'<id>')])
"
```

### 2. 代码增量:fallback 链复查
代码以现有月份为 obtained,重跑 `run_discovery`(L0→L3 集合差驱动)补 gap_set 中比现有更新的月 + confirmed_gaps 每月轻量复查(CDX/fundmonitors 该月是否新可得)。
- 新月:过 gate -> `monthly_returns`
- confirmed_gaps 该月新可得:补录 `monthly_returns` + 移出 `confirmed_gaps`
- 仍无:保留 `confirmed_gaps`(刷新 checked_at)

### 3. inception_assumed 重探下界
若 `inception_assumed=True`:复查顺带低成本重探下界(只查 CDX 更早快照)。发现更早月 -> 下界前移 -> expected_range 扩展 -> 新暴露早期月入队补洞。**禁后缩**(=静默删缺口)。

### 4. upsert 新月 + 更新 confirmed_gaps
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -c "
from lib.db import get_connection,ensure_tables,upsert_monthly_return,record_confirmed_gap,remove_confirmed_gap
c=get_connection(); ensure_tables(c)
for date,net_return in <新月列表>: upsert_monthly_return(c,fund_id='<id>',date=date,net_return=net_return,commentary_truth=net_return)
# confirmed_gaps 增删由复查结果决定(record_confirmed_gap / remove_confirmed_gap)
"
```
`upsert_monthly_return` 自动重算 NAV。|r|≥0.5 超限新月进 `pending_review`(不丢弃)。

### 5. 输出 + pending_review 滞留报告
- 新增月数、最新截止月(无新数据提示"已是最新")
- **pending_review 滞留报告**:输出 `review_state='pending'` 且 `created_at` 滞留 >14 天的条目清单(`list_stale_pending_reviews`),防人工审核队列变静默坟场
- 提示 webapp `POST /api/funds/<fund_id>/recompute`

## 数据完整性铁律
同 `/add_fixed_fund`:不捏造、提取诚实性、异常值保留、无幻觉回填、ANTI-FABRICATION。

## 完成标准
- [ ] 新月正确写入 `monthly_returns`,NAV 重算正确
- [ ] confirmed_gaps 增删一致(补录移除/仍无保留)
- [ ] pending_review 滞留报告已输出
- [ ] 数据可追溯,未计算指标(留给 webapp)
