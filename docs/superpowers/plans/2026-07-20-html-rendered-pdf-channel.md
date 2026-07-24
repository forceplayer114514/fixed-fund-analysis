# Spec F: HTML 报告 → 渲染 PDF 摄取通道

## Context

2026-07-20 发现 Coolabah Institutional 2026-06 月被误提取成 9.04%（真实值
0.71%）。根因：HTML 通道（`llm_ingest/extract_html.py`）对原始 HTML 做字节级
文本窗口切片喂给 LLM，页面上"视觉上互不相关、但字节距离很近"的两个数值区块
（一张"成立以来汇总卡片"和真正的"月度/滚动收益表格"）被同一个窗口框了进去，
LLM 认错了行。

本会话已对 `extract_html.py` 做了两处修复（base64 内联图片剥离、CRLF/LF 统一），
缓解了窗口预算被图片挤占、以及本地缓存文件 vs 实时抓取结果不一致两个问题，
但**没有根治**：LLM 仍会在窗口内同时看到"当前最新月的汇总卡片"和"其他月份的
表格"时，偶发性地选错。这条路径目前对 Institutional class 还留着 4 个真实
缺口（`2022-12`、`2024-11`、`2024-12`、`2026-03`），管道的月份闸正确地把它们
拒绝成了 `confirmed_gaps`（安全但不完整），没有静默写错数据。

用户明确要求：这类"版面结构在字节流里丢失导致认错表格"的 bug，必须用**代码层
硬约束**解决，不能靠继续堆 prompt 规则（prompt 是软约束，已经实测被模型忽略过）。
硬约束方案 = 把 HTML 转成 PDF，交给现成的、按页读版面的 PDF 提取通道
（`extract.extract_from_pdf`），版面结构在 PDF 里天然保留，"认错表格"这类错误
从代码结构上不再可能发生。

已知的一个真实拦路虎：目标页面除了常规表格/文字，还内嵌 Plotly.js 图表，逐月
NAV 数值只存在于 hover-only tooltip 文本（`hoverinfo:"text"`），普通 `page.pdf()`
打印导出读不到这部分数据（已实测验证：导出 PDF 文本层里一条历史数值都没有）。

该问题已委托一个隔离的独立测试（不在本项目上下文里跑，避免污染），产出报告见
`/Users/chong/Desktop/test2/REPORT.md`。结论：

| 方案 | 原理 | 结果 |
|---|---|---|
| A（采纳） | Playwright `page.evaluate` 直接读 `.js-plotly-plot` 的 `gd.data`（图表库自己挂的原始数据对象，不用触发 hover），拼成表格注入 DOM，再整页 `page.pdf()` | 完全确定性、3 个校验数值 + 常规表格全部验证通过，~60 行，<10 秒 |
| B | 真实鼠标移动模拟 hover 事件 | 全量 88 点自动跑 0/88 命中，事件滞后 1~3 点且不稳定，不满足确定性要求，否决 |
| C | 截图拼接 | 依赖先解决 B 的问题，未实现 |

## 目标 / 验收标准

1. HTML 通道（`webapp/backend/app/routers/ingest.py` per-link 循环里
   `channel == "html"` 分支）不再把原始 HTML 文本做字节窗口切片喂给 LLM；改为
   先渲染成 PDF，再走既有 PDF 通道 `ex_mod.extract_from_pdf`。
2. 渲染步骤本身是纯代码/浏览器自动化，不引入新的 LLM 判断点（读 `gd.data` 是
   逐字转写，不是推断/计算）。
3. 渲染出的 PDF 必须用 `max_pages=0`（全文）喂模型 —— **页级裁剪会复现同一类
   bug**：Coolabah 报告页 1 就有"since inception 年化汇总"这段文字，若只裁前
   N 页喂模型，等于把"字节窗口切片"换成"页面窗口切片"，本质没解决问题，只是
   换了个粒度。这条必须有单测覆盖（断言调用 `extract_from_pdf` 时
   `max_pages == 0`）。
4. 找不到图表数据（非 Plotly / `gd.data` 为空 / 没有满足条件的 trace）时仍要能
   正常打印，附录段落为空即可，不报错 —— 兼容未来可能出现的普通 HTML 报告页。
5. 渲染失败（playwright 不可用 / 超时 / 输出空文件）按现有 `except Exception`
   处理，记 `record_confirmed_gap(exhausted_levels="html_render_fail")`，**不
   允许静默退回旧的字节窗口方法**（退回等于重新引入已经被判定为不可靠的软约束
   路径）。
6. 同一 URL 只渲染一次（job 内 cache），供该 URL 名下所有月份复用——
   `single_file_multi_month` 场景现状是 43 个月同一 URL，只应该看到 1 次浏览器
   渲染，不是 43 次。
7. 反捏造两道闸（`check_quote_tokens` / `check_rolling`）改用渲染后 PDF 的
   `pdf_mod.full_text()` 做 `source_text`，与既有 PDF 通道行为一致（不再用
   原始 HTML 文本做校验源）。
8. `extract_html.py` 的字节窗口切片函数（`_shrink_plotly_html` 等）保留在仓库
   （现有单测还在覆盖它，不删代码），但停止在生产 ingest 路径被调用。

## Out of scope（本次不做）

- 不解决"图表数据完全服务端渲染成图片、前端没有可读 JS 数据对象"的情况——
  项目内唯一的 HTML 通道来源（Coolabah, Plotly）满足"数据挂在可读对象上"这个
  前提。未来若遇到反例，需要往 REPORT.md 方案 B/C 的方向单独解决（且要先解决
  那边验证过的"hover 事件定位延迟 1~3 点、不确定性"问题），不在本次范围。
- 不改 CSV 通道——CSV 是纯表格行，不存在"版面结构在字节流里丢失"这个问题
  类别，本次问题特定于"HTML + 交互图表"。

## 设计

### 新模块 `llm_ingest/html_to_pdf.py`

```
render_html_to_pdf(url: str, out_path: Path, *, timeout: int = 120) -> Path
```

- Playwright sync API（与 `discover.py::_fetch_playwright` 同款写法：
  `sync_playwright()` + `chromium.launch(headless=True)`，复用现有依赖，不
  新增第三方库）。
- `page.goto(url, wait_until="networkidle", timeout=timeout*1000)`。
- `page.evaluate(...)` 读所有 `.js-plotly-plot, .plotly-graph-div` 元素的
  `gd.data`：每条 trace 需 `hoverinfo == "text"` 且 `text` 数组长度 >= 5（过滤
  单点图例标记 trace，只留真实序列）——与 `test2/candidate_A_dom_extract.js`
  验证过的过滤规则一致。返回结构化的 `[{plotId, rows:[{series, text}]}]`，
  逐字转写，不做任何计算/推断。
- Python 侧用 `html.escape` 拼 `<table>` 附录 HTML，`page.evaluate` 注入到
  `document.body` 末尾。
- `page.pdf(path=..., format="A4", print_background=True, margin=...)`。
- `sections` 为空 → 跳过注入，照常打印（兼容非 Plotly 的普通 HTML）。
- 输出文件不存在/空 → 抛 `HtmlToPdfError`。

### `ingest.py` 改动点

- per-link 循环里 `channel == "html"` 分支：新增 `rendered_urls: set = set()`
  （job 内局部变量，只记录"这个 url 本 job 内已渲染过"这一事实，**不**缓存
  解析出的绝对 `Path`）。渲染目标路径每次都从当前 `pdf_dir` + 按 url 哈希出的
  固定文件名（`_rendered_pdf_filename(url)`）重新拼出。
  - 踩过的坑：第一版直接把 `render_html_to_pdf` 返回的绝对 `Path` 存进
    `Dict[str, Path]` 缓存——但自动纠名（`rename_fund_id` 触发后，per-link
    循环里会把 `pdf_dir` 整个目录 rename 到新 `fund_id` 下）会让缓存的旧
    `Path` 指向一个已经被搬空的目录。改成只缓存"已渲染"这个布尔事实、路径
    每次重新拼，就和现有 PDF 通道 `pdf_path = pdf_dir / f"{ym}.pdf"`（每次
    重新算，天然免疫这个问题）保持同一套模式。
  - 按 url 哈希而非固定文件名：避免"未来某个发行商用 per-month 各自不同的
    HTML 链接"这种目前不存在、但架构上该支持的场景里，两个不同 url 的渲染
    产物互相覆盖。
  - `single_file_multi_month` 分支额外做一次**提前渲染**（在 per-link 循环
    开始前，紧跟着算出 `links` 之后）：直接调用 `render_html_to_pdf` 并把
    url 记入 `rendered_urls`，失败则整个 job 直接 `raise ValueError`（与旧版
    "预抓文本失败即整个 job fail-fast"语义一致，不进入循环后对 43 个月份各
    自重试渲染、误记 43 条 gap）。也顺带去掉了旧版"预抓一份原始 HTML 文本存
    进 payload_cache"这一步——html 通道现在完全不用它，继续做等于白白多下载
    一次 16.9MB 页面。
- 提取调用从 `ex_mod.extract_from_source(url, ym, html_text=...)` 改成
  `ex_mod.extract_from_pdf(pdf_path, ym, max_pages=0, fund_name=...,
  issuer=...)`（`pdf_path` 就是上面重新拼出的渲染产物路径）。
- `source_text`（反捏造闸用）从 `payload_text` 改成
  `pdf_mod.full_text(pdf_path)`——与 `channel == "pdf"` 分支同一行代码，
  直接复用既有 if/else，不新增分支。
- `payload_cache` 继续只给 CSV 通道用（不动）。

## 涉及文件

- `llm_ingest/html_to_pdf.py`（新建：`render_html_to_pdf` / `_filter_hover_rows`
  / `_build_appendix_html`）
- `webapp/backend/app/routers/ingest.py`（`_rendered_pdf_filename` 新增；
  `channel == "html"` 分支改接；`single_file_multi_month` 增加提前渲染一次）
- `tests/test_html_to_pdf.py`（新建：`html_to_pdf.py` 单测，全部 mock
  playwright，不启真实浏览器）
- `tests/test_ingest_html_render_channel.py`（新建：`ingest.py` 层面集成测试，
  验证渲染只发生 1 次 + `max_pages=0` + 渲染失败正确 fail-fast/记 gap）

## 测试计划

### 单测（先写，覆盖以下场景后再写 `html_to_pdf.py` 实现）

1. 含 Plotly `data-for` JSON 脚本 + `hoverinfo:"text"` 序列的 fixture HTML：
   渲染出的 PDF 文本层（`pdf_mod.full_text`）同时包含原页可见表格文字 + 注入
   的 hover 附录文本。
2. 单点/图例标记 trace（`text` 数组长度 < 5）不进入附录。
3. 无 Plotly 内容的普通 HTML fixture：正常出 PDF，无附录段落，不报错。
4. playwright 不可用（mock `ImportError`）→ 抛 `HtmlToPdfError`。
5. 输出文件异常（mock 空文件）→ 抛 `HtmlToPdfError`。
6. `ingest.py` 层面：mock `render_html_to_pdf` 返回一份真实构造好的小 PDF
   fixture，断言同一 `url` 循环多个月份时只调用渲染函数 1 次（cache 命中），
   且调用 `extract_from_pdf` 时 `max_pages == 0`。

### 端到端（最终验证，全部单测通过后才跑；真实网络 + 真实浏览器 + 真实 job
runner，不 mock，不绕过 HTTP 层直接调函数）

- 目标基金：`coolabah_floating_rate_high_yield_fund_institutional_class`，
  走 `POST /api/ingest/funds` 真实重跑一次完整 ingest job。
- 断言：
  - 之前 4 个真实缺口（`2022-12`、`2024-11`、`2024-12`、`2026-03`）是否恢复
    ——恢复则数值需与页面 hovertext 原文吻合；仍缺口则要在报告里明确是"渲染
    没读到该月数据"而非"程序报错"，如实记录，不能悄悄消失不提。
  - `2026-06` 月重新提取结果为 `nav_pair`，数值 ≈ 0.0071（不是之前错误的
    0.0904），完整走过 `check_quote_tokens` + `check_rolling` 两道闸。
  - `stats` 里下载失败/API 错误类不应因为这次改动新增（若渲染引入新失败模式，
    在报告里如实列出）。
  - 抽查 job 全程 `log_tail`，确认真的经过
    `discovering_l2_pdf` → `ingesting_l2_pdf` 状态机（而非绕过状态机的直接
    函数调用——这是上一轮会话被指出过的教训："你跑的测试是端到端的吗？"）。

## 风险 / 已知局限（如实记录，不回避）

- 每次调用都喂全量渲染 PDF（~4MB/27 页，base64 后 ~5.3MB），单基金 43 个月
  = 43 次请求重复上传同一份 PDF——比旧的 80KB 文本窗口重得多，是"正确性优先于
  流量成本"的有意取舍（页级裁剪会复现同一 bug，见验收标准 3），此处明确记录，
  不是遗漏。
- 依赖"图表库把原始数据挂在可读 JS 对象上"这个前提（Plotly/ECharts/Highcharts
  均满足）。若未来遇到服务端渲染成图片、前端真的没有可读数据对象的图表，本
  方案失效，需另起方案（REPORT.md 方案 B/C 方向，且要先解决那边验证过的
  hover 事件定位延迟问题），不在本次范围内实现。

## 端到端验证记录（实测, 如实记录）

第一次真实 end-to-end 跑（走 HTTP `POST /api/ingest/funds`，非直接函数调用）
就暴露了一个未预料到的真实 bug，记录如下，供以后同类改动参考：

**现象**：job 跑完后 `funds` 表 `fund_id` 被自动纠名机制错改成
`coolabah_floating_rate_high_yield_fund_institut`（`fund_name` 变成
`"Coolabah Floating-Rate High Yield Fund - Institut"`——被硬生生截断）。

**根因**：目标页面里 "Fund: ..." 抬头行和 "Return (since ...)" 摘要行是
`white-space: nowrap` 的窄列表格单元格。`render_html_to_pdf` 原用 1400px
视口，这两行文字在这个视口宽度下会被内容溢出裁掉——**不是 Playwright 独有的
问题**，用浏览器实际打开页面在类似窄视口下也会看到同样裁断（拉取原始 HTML
源码核对过，字符串本身是完整的 "...Institutional Class"，只是渲染层面在
窄视口下裁掉了）。渲染出的 PDF 忠实反映了这个被裁断的视觉内容，LLM 逐字转写
`fund_name_text` 时按 CLAUDE.md 反捏造铁律"只转写不推断"如实抄录了这个被裁断
的抬头（这一步 LLM 没有错——错的是喂给它的 PDF 内容本身已经是裁断的）。这个
（早于本次 Spec F 就存在的）自动纠名机制看到"官方名称跟当前 fund_id 不一致"
就照常触发了改名，把生产库的 fund_id/fund_name 都改错了。

**修复**：视口从 1400px 加宽到 2400px（`html_to_pdf.py::REPORT_VIEWPORT`），
实测两处文字都能完整渲染，验证方式：直接下载原始 HTML 核对源码里的字符串
完整、且用 2400px 视口重新渲染 PDF 后文本层同时包含完整
"Institutional Class" 和完整 "9.04% pa net)"。

**生产库修复**：用项目自带的 `store.rename_fund_id` 把 fund_id/fund_name 改
回正确值（`coolabah_floating_rate_high_yield_fund_institutional_class` /
`"Coolabah Floating-Rate High Yield Fund - Institutional Class"`），子表
（`monthly_returns`/`confirmed_gaps` 等）行数据本身没有被破坏（只是挂在
错误的 fund_id 下），随 rename 一起迁移，没有数据丢失；`pdf_cache` 目录名
手动同步改名。

**教训**：这类"渲染 HTML 页面"的方案，验证测试用例不能只覆盖"核心数据点存在"
（本次 REPORT.md 阶段验证过的 3 个 NAV 检查点 + 常规表格），还必须覆盖"页面
上所有会被下游当作可信信号使用的文本区域"（这里是 `fund_name_text`，会触发
自动纠名这种有副作用的动作）——窄视口在研究阶段的 3 个数值检查点上"恰好没
暴露问题"，直到接入会读这段文字做决策的下游逻辑（自动纠名）才暴露。以后新增
"渲染网页当数据源"的场景，验收清单应显式加一条："凡是会被下游当结构化字段
使用的文本区域（不只是数值表格），都要在足够宽的视口下验证不被裁断"。
