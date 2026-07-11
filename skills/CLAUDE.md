# CLAUDE.md - skills 模块规则（数据抓取/清洗/入库端）

本文件夹是**独立的 Claude Code 工作区**，负责澳洲固定收益基金数据的抓取、清洗与入库（写入 SQLite `monthly_returns` 表）。与 webapp **仅通过共享 SQLite 数据库**（`data/fund_analysis.db`）联系，**不 import webapp 任何代码**。

## 一、数据完整性（最高优先级，不可违反）

1. **禁止捏造任何金融数据**。任何净值、收益率等数值，必须能追溯到真实抓取的数据源（URL + 抓取时间）。数据缺失或抓取失败时，必须明确报错并停止，不允许用估算值、历史平均值或"合理猜测"填补。
2. **数据缺口（gap）零容忍**：如果某月数据缺失，必须报错并列出缺失的具体月份，不允许跳过或用插值填补。
3. 月度收益率序列起点必须是第一份真实研报的日期，绝不允许反推捏造填补基金成立初期的无披露月份。

## 二、禁止对原始数值"合理性纠正"

如果解析出的数据数值异常（如单月回报率远超历史正常波动范围），程序不得自动修改、剔除或"猜测式还原"。唯一允许：如实保留原始提取值，同时生成醒目异常标记交人工判断。
若发现**字段类型提取错误**（如把季度滚动回报误当月度回报、年化值误当月度值），视为数据管道结构性缺陷，必须按数据缺口处理（不参与计算），不能套用"保留原值+异常告警"。

## 三、防范大模型幻觉回填

1. 大模型易因追求逻辑自洽（消除 gap 或匹配终值）产生"合理幻觉"篡改基础数据。
2. 任何补齐（backfill）、前推（forward-fill）逻辑在数据摄取层**绝对禁止**。提取层只能做纯文本到数字的映射。
3. 禁止连续出现相同的精确浮点数插值（ANTI-FABRICATION GUARD）。

## 四、子 agent 委派

涉及网络抓取（MCP `fetch`/`stealthy_fetch`、WebFetch/WebSearch）、大批量数据处理的任务，委派给子 agent 执行。子 agent 返回后，主对话核对数据完整性，可疑（数值突变、格式异常）则重新委派验证而非直接采信。

## 五、职责边界

- skills **只写** `funds` + `monthly_returns` 表（原始数据 + NAV 复利重算）
- skills **不算指标**（Geltner 去平滑、Omega、回撤等由 webapp 负责）
- skills **不检测异常**（MAD/Z-Score 由 webapp 负责）
- skills **不更新 RBA**（由 webapp 定时调度 + `POST /api/rba/refresh` 负责）
- 入库后提示用户在 webapp 触发 `POST /api/funds/{fund_id}/recompute` 计算指标

## 六、环境

- Python 3.9.6，用 `Optional[X]`（非 PEP 604 `X | None`），`python3`/`pip3`
- DB 路径：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`
- APIR 正则：`^[A-Z]{3}\d{4}AU$`（可为空，Stake/MXT 无标准 APIR）
