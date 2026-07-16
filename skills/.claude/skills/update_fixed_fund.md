---
name: update_fixed_fund
description: "更新已注册基金最新月度收益:代码 fallback 链增量复查 gap_set + confirmed_gaps 每月复查,upsert 新月。仅入库原始数据,不算指标。"
---

# /update_fixed_fund <基金ID或名称>

## 职责边界
代码增量复查最新月 + confirmed_gaps 复查 + upsert 新月到 `monthly_returns`。不算指标/不检测异常/不更新 RBA。入库后提示 webapp `POST /api/funds/{fund_id}/recompute`。

## 输入 / 环境
基金 ID(如 `stake_accumulate`)或名称;不传则列出全部供选择。同 `/add_fixed_fund`(skills 工作区、DB 路径、写库方式)。

## 工作流

### 1. 确认 fund_id
```bash
cd skills && python3 -c "from lib.db import get_connection,ensure_tables,list_funds; c=get_connection(); ensure_tables(c); [print(f['fund_id'],'|',f['fund_name']) for f in list_funds(c)]"
```

### 2. 跑代码全流程(增量,默认 solver)
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest update --fund-id <id> [--extractor solver]
```
**代码全自动**:以已入库月为约束求解锚点,重跑 `run_discovery`(集合差驱动只补 gap_set)、`inception_assumed=True` 时顺带重探下界(禁后缩)、新月 solver 求解入库或 pending、confirmed_gaps 增删、`pending_review` 滞留 >14 天报告。内部机制见 `skills/docs/pipeline.md`。

### 3. 输出
返回 JSON 含 `new_months`/`pending_review_count`/`gaps`/`stale_pending_reviews`。无新数据时报"已是最新"。`pending_review` 滞留清单人工审核走 `promote-pending`(见 `/add_fixed_fund` 人工通道)。

**运行期代码冻结**:同 `/add_fixed_fund` 步骤 5——执行期间禁改 `lib/`、`tests/`
任何代码,`extractor_mismatch`/大量新 pending 不就地诊断改代码,输出诊断包后停止,
提示用户跑 `/extend_extractor`。

## 完成标准
- [ ] 新月正确写入 `monthly_returns`,NAV 重算正确
- [ ] confirmed_gaps 增删一致
- [ ] pending_review 滞留报告已输出
