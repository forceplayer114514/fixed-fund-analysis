# CLAUDE.md - skills 模块规则（数据抓取/清洗/入库端）

本文件夹是**独立的 Claude Code 工作区**，负责澳洲固定收益基金数据的抓取、清洗与入库（写入 SQLite `monthly_returns` 表）。与 webapp **仅通过共享 SQLite 数据库**（`data/fund_analysis.db`）联系，**不 import webapp 任何代码**。

## 一、数据完整性铁律

禁捏造（任何数值须可追溯到真实抓取的 URL+时间）、gap 零容忍（缺失月须报错列出，不插值）、异常值如实保留+标记不自动纠正、字段类型提取错误按缺口处理、摄取层禁 backfill/forward-fill、禁连续相同精确浮点插值。**以上由 `gate_check`（`lib/extract.py`）+ solver 反捏造 assert（`lib/solver.py`）代码强制执行，本节是价值观说明，不是操作手册**——遇到分歧以代码报错为准。

## 二、执行主体

ingest 确定性抓取（MCP fetch/curl 下 PDF、`lib.ingest`/`discover` CLI、fallback 链、solver）主会话直接执行。探测脚本循环（Vue AJAX 端点试错、分页参数、正则试错、逐资源页爬）派 `general-purpose` 子代理，只返回结构化摘要（已验证 URL + 归档结构 + 链接规律），脚本代码不进主上下文。主会话抓取后自行核对数据完整性，可疑（数值突变、格式异常）则重新抓取验证而非直接采信。**运行期代码冻结**：`add_fixed_fund`/`update_fixed_fund` 执行期间禁改 `lib/`、`tests/` 任何代码，格式漂移导致的 `no_candidates`/大量 pending 只输出诊断包后停止，转 `/extend_extractor`（主会话/强模型执行，禁派 flash 子代理写这部分代码）。

## 三、职责边界

skills 只写 `funds` + `monthly_returns` 表；**不算指标**（Geltner 去平滑、Omega、回撤等由 webapp 负责）、**不检测异常**（MAD/Z-Score 由 webapp 负责）、**不更新 RBA**（由 webapp 负责）。入库后提示用户在 webapp 触发 `POST /api/funds/{fund_id}/recompute`。

## 四、环境

Python 3.9.6，用 `Optional[X]`（非 PEP 604 `X | None`），`python3`/`pip3`。DB 路径：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`。APIR 正则 `^[A-Z]{3}\d{4}AU$`（可为空，`lib/db.py` 已校验）。**写库必须走 `python3 -m lib.ingest <command>`**——直接 `sqlite3` 写、或内联脚本持 `FUND_DB_WRITE_TOKEN` 绕开 CLI，均被 `.claude/hooks/db_write_guard.py` 拦截。

## 五、管道原则

抓取/解析/判口径/验证/缺口/入库全由代码（`lib/strategies.py` + `lib/ingest.py`）确定性执行，LLM 只做定位（找已验证归档页 URL，须真 fetch 过：HTTP 200 + content-type + 首屏摘要）+ 兜底（被代码点名补料/异常）。官网免费源优先于第三方聚合站，付费墙立即跳过换源。缺口非失败，穷尽后入 `confirmed_gaps`。**维护 `lib/candidates.py`/`lib/solver.py`、处置 `no_candidates` 等 pending、新增发行商候选模式时，先读 `docs/pipeline.md`**（fallback 链细则、solver 三阶段、A4 数学准入、review_reason 枚举、加候选模式 7 步流程全在其中；执行入口是 `/extend_extractor`，验收含 `python3 -m lib.ingest regress` 全库零回归）。
