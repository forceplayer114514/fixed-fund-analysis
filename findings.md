# Findings & Decisions (阶段 2)

## 阶段 1 已实现接口契约（复用，不重写）
- **models.py**: Fund, MonthlyReturn, Anomaly, RbaCashRate, FundMetric, AiReport（6表）。fund_name UNIQUE, apir_code nullable+UNIQUE, 子表 ondelete=CASCADE。
- **calculations.py**: `compute_all_metrics(returns, rf_rates, fund_name) -> dict`（18键，与 FundMetric 字段一致，不含 fund_id/updated_at）。含 calculate_autocorrelation, unsmooth_returns, should_apply_geltner, _build_nav_series, LJUNG_BOX_CRITICAL_VALUE=3.841。
- **anomaly.py**: `detect_anomalies(time_series, threshold_sigma=3.0) -> list[dict]`（MAD）。
- **crud.py**: create_fund, get_fund, get_all_funds, delete_fund, upsert_monthly_return(含 recompute_nav), get_returns, recompute_nav, resolve_rf_rates, replace_anomalies, upsert_metrics。
- **rba.py**: fetch_current_rba_rate() -> float, fetch_historical_rba_rates() -> dict[str,float], upsert_rba_rates(session, rates) -> int。
- **metrics_pipeline.py**: `compute_and_store_metrics(session, fund_id, fallback_rba_rate=None) -> dict`（含 _find_month_gaps 缺口检查，缺口抛 ValueError）。
- **database.py**: engine, SessionLocal, init_db(), get_db(), Base。
- **config.py**: settings (DATABASE_URL, RBA_BASE_URL, RBA_HISTORY_API)。

## 环境约束
- Python 3.9.6，Optional[X]，python3/pip3
- 阶段 1 共 43 测试通过（在 main）

## 关键计算细节
- 超额收益：逐月扣减 RBA 复利年化 (spec 4.1)：r_e,t = r_t - RBA_t/12，AnnExcess = (∏(1+r_e))^(12/n) - 1
- Geltner 三重防火墙：n>=36, Q>3.841, 0<=phi<=0.85
- Omega：分母0返回 inf
- compute_all_metrics 返回键名与 FundMetric 字段一致

## Task 2 发现（影响后续 Task）
- **Starlette 同步端点在 threadpool 执行**：内存 SQLite 默认每线程独立 DB -> 'no such table'。修复：conftest 用 `StaticPool + check_same_thread=False`。
- **Omega=inf 不 JSON 合规**：Omega 比率无跑输月时为 inf，FastAPI JSONResponse 拒绝 inf（Out of range float）。修复：`schemas.sanitize_for_json` 递归转 inf/NaN -> None。**compare/time-series 端点也须复用此函数**。

## 已知问题（下迭代）
- **PATCH 人工纠错事务非原子**：`PATCH /api/monthly-returns/{id}` 中 upsert_monthly_return 内部先 commit（净值+NAV 持久化），再调 compute_and_store_metrics。若后者抛 ValueError（极端净值致 peak<1e-4 或去平滑分母过低），返回 400 但净值已改、指标仍是旧值。触发条件边缘，下迭代改为同事务或先验证后 commit。

## Resources
- spec: docs/superpowers/specs/2026-07-10-fund-web-ui-design.md
- 阶段2计划: docs/superpowers/plans/2026-07-11-fastapi-api-layer.md
- 阶段1计划: docs/superpowers/plans/2026-07-10-backend-foundation.md
- 阶段1代码: webapp/backend/app/

## Visual/Browser Findings
- 无

## 阶段 3（skills 模块）发现（2026-07-11）
- **skills 文件夹独立**：`skills/.claude/skills/`（项目级，用户在 `skills/` 运行 cc 才能用 slash 命令），`sqlite3` 原生 SQL，**不 import webapp**。MCP ScraplingServer 在全局 `~/.claude.json`（用户级），skills/ 工作区可用。
- **db.py**：11 测试通过，schema 与 webapp models 完全对齐（funds+monthly_returns），`upsert ... ON CONFLICT DO UPDATE` + `COALESCE` 保留旧 commentary_truth，`recompute_nav` 按 date 升序复利（nav=1.0 起点），`PRAGMA foreign_keys=ON`。默认 DB 路径 `<仓库根>/data/fund_analysis.db`（环境变量 FUND_DB_PATH 覆盖）。
- **extract.py**：11 测试，复用 parse_factsheet 通用函数（MONTH_MAP/clean_spacing/get_last_day_of_month/extract_month_prefix）+ 新增 parse_date_string（任意日期->月末 YYYY-MM-DD）+ check_gaps（纯日期列表，只检测不抛错）+ download_file（复制自 fetch_web）+ parse_pdf_text（PyMuPDF，fitz 可选导入）。去除所有项目特定依赖。
- **Stake 是 PDF 基金**（非 HTML）：列表页 `hellostake.com/au/legal/monthly-performance-report` 列出每月 PDF 链接（assets.contentstack.io），15 个月（2025-03~2026-05）。
- **Stake PDF 下载要点**：需 `Referer: https://hellostake.com/` header（否则 422）；May 2026 PDF 需 `?branch=odyssey` 参数。
- **Stake PDF 提取逻辑**：每个 PDF 的 "Fund performance" 表中 `Stake Accumulate Class A return (after fees and expenses)` 后第一个百分数 = 该月 1-month return（net_return）。前 8 个月（基金不足 12 月）只有 3 列（1m/3m/6m），后 7 个月有 5 列。正则用 `r'fees and expenses\)\s*\n\s*([\d.]+)%'`（容忍 "after" 后换行）。
- **基金 inception 2024-11-29，第一份 PDF 2025-03**：序列起点设为 2025-03（CLAUDE.md 第六条：不反推捏造成立初期无披露月份）。
- **端到端验证通过**：Stake 15 个月入库真实 `data/fund_analysis.db`，NAV 复利手算与 DB 一致（末月 1.1825），无缺口，可追溯（verified_at=2026-07-11）。
- **子代理 MCP 图片问题**：子代理模型不支持图片输入，用 MCP screenshot/fetch 返回图片会崩（API Error 400 "Model only support text input"）。解决：主对话接管 MCP 抓取，或子代理只用 Bash Python（requests+PyMuPDF）纯文本流程。
- **skills 职责边界**（spec 2.1）：只写 funds + monthly_returns，**不算指标/不检测异常/不更新 RBA**；webapp `POST /api/funds/{id}/recompute` 负责指标+异常，`POST /api/rba/refresh`+定时调度负责 RBA。技能 .md 入库后提示用户触发 webapp recompute。
- **旧技能删除**（spec 6.5）：`skill/`（3 个 .md）+ `.claude/skills/{fund_analysis,fund_analysis_history,fund_analysis_list}/SKILL.md`（3 个 cc 识别文件）。`.claude/skills/` 现为空目录。
- **已知问题**：Stake 端到端用主对话执行（子代理 MCP 图片失败）；未来 add/update 技能在 skills/ 工作区运行时，由 cc（opus）执行 MCP 抓取，不会有图片问题。
