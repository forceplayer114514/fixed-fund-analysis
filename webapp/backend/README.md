# 固定收益基金分析 - 后端 API

基于 FastAPI 的后端，提供基金业绩分析 REST API 与 RBA 利率定时调度。

## 启动

```bash
cd webapp/backend
pip3 install -r requirements.txt
# 开发模式（带热重载）
uvicorn app.main:app --reload --port 8000
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| DATABASE_URL | sqlite:///data/fund_analysis.db | SQLite 连接串 |
| CORS_ORIGINS | http://localhost:5173 | 允许的前端来源（逗号分隔） |
| SCHEDULER_ENABLED | true | 是否启用 RBA 定时调度 |
| RBA_CRON_HOUR | 9 | 每日抓取 RBA 的小时 |

## API 端点

- `GET /health` 健康检查
- `GET /api/funds` 基金列表（含数据截止年月）
- `POST /api/funds` 注册基金元信息（不抓取，抓取由 add_fixed_fund skill 完成）
- `DELETE /api/funds/{fund_id}` 删除基金（级联）
- `POST /api/funds/{fund_id}/recompute` 重算指标
- `GET /api/metrics/compare?fund_ids=A,B&period=full` 5 维对比（period: full/3y/1y/common）
- `GET /api/metrics/time-series?fund_ids=A,B&period=full` 对齐 NAV 时序（含去平滑）
- `GET /api/anomalies` 异常列表
- `PATCH /api/monthly-returns/{id}` 人工纠错（触发重算）
- `POST /api/rba/refresh` 手动刷新 RBA 利率

## 测试

```bash
cd webapp/backend && python3 -m pytest tests/ -v
```
