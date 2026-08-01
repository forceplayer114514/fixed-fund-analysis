# 修复计划: "网页本身就是月报"的自动识别与路由

日期: 2026-08-01
触发案例: `Coolabah Global Floating-Rate High Yield Complex` 摄取失败
状态: 待执行 (计划已确认, 尚未动代码)

---

## 0. 一句话

发现层与"HTML 渲染成 PDF"通道之间缺路由: 发现层只会问"这页上挂了哪些月报 PDF",
从不问"这页自己是不是就是月报"。三个独立断点串在一起, 导致 Coolabah 这类
纯网页月报的基金必须人工预先填 URL + 成立月份才能摄取。

---

## 1. 触发案例与现场事实 (全部实测, 非推断)

基金: `Coolabah Global Floating-Rate High Yield Complex`
- DB 现状: `fund_id=coolabah_global_floating_rate_high_yield_complex`,
  `confirmed_url=''`, `url_type='archive'` (即未配置, 走标准 PDF 归档流程)
- 前端报错: `discovery: 0 links, gaps=0` → `FAIL: discovery 未产出任何 PDF 链接`

### 1.1 Grok 的答案 (engine=grok 实测)

```
ranked[0].url = https://coolabahcapital.com/coolabah-global-floating-rate-high-yield-fund/
grok_attempts[0].evidence = "...separate /performance-report-coolabah-global-floating-rate-high-yield-complex-etf
                             pages contain monthly data for April 2026 (and similar)."
grok_attempts[1] = HTTP 503 upstream_unavailable   ← 第二次并发调用当时挂了, 偶发
```

Grok **答案字段填的是基金介绍页**, 但它自己的说明文字里点名了真正的月报页。
代码只读答案字段, 不读说明文字 (这是对的, 说明文字是自由文本, 不该当结构化答案用)。

**结论: 不需要单独修 Grok。** 介绍页是合法的"入口页", 后续导航本就该从这里跳到
月报页 —— 只要导航那步不瞎 (见断点一)。

### 1.2 两个页面的实测数据

| | 介绍页 | 月报页 |
|---|---|---|
| URL | `https://coolabahcapital.com/coolabah-global-floating-rate-high-yield-fund/` | `https://coolabahcapital.com/performance-report-coolabah-global-floating-rate-high-yield-complex-etf` |
| 实际落地 | 同上 | 302 → `/wp-content/uploads/2025/03/performance-report-coolabah-global-floating-rate-high-yield-complex-etf.html` |
| 大小 | 337 KB | 13.4 MB (pandoc 静态 HTML) |
| `.pdf` 链接 | 2 条 (Fund Payment Notice / Top 10 Holdings, 都不是月报) | 2 条, 且都是 `budget.gov.au` 的税务说明, 完全无关 |
| Plotly | **无** (`js-plotly-plot` 不存在) | **有** (`js-plotly-plot` 存在) |
| hover 净值序列 | 无 | **18 点, 2025-01-31 $100.00 … 2026-06-30 $108.95** |

介绍页零 Plotly → "有没有内嵌净值序列"是干净的判别信号, 不会在介绍页误触发。

### 1.3 正文口径 (入库时必须注意)

月报页 Commentary 原文: `returned 0.80% gross (0.71% net)`。
净值序列 108.18 → 108.95 算出 0.71%, 与 **net** 对得上。
既有 `llm_ingest/issuer_rules.py` 已有 `coolabah` 规则要求取 net, 无需改。

---

## 2. 三个断点 (根因)

### 断点一: 链接文字套壳 → 真正的月报链接被跳过

`llm_ingest/discover2.py:53`:
```python
_HTML_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', re.I)
```
`>([^<]+)</a>` 要求 **锚文字是纯文本, 中间不能有任何标签**。

介绍页上那条链接的真实 HTML (实测摘录):
```html
<a href="https://coolabahcapital.com/performance-report-coolabah-global-floating-rate-high-yield-complex-etf" target="_blank">
  <span class="elementor-icon-list-icon"><i aria-hidden="true" class="fas fa-file-download"></i></span>
  <span class="elementor-icon-list-text">Full Performance Report</span>
</a>
```
锚文字被 `<span>` 包了 → 正则匹配不上 → `_extract_monthlyish_page_links`
(discover2.py:75) 完全看不见这条链接。

实测 `_probe_one(介绍页)` 返回:
```
nav_urls = ['https://coolabahcapital.com/performance/',
            'https://coolabahcapital.com/coolabah-global-floating-rate-high-yield-fund/#performance']
```
真正有用的那条一次都没出现。

**这是通用缺陷**: Elementor / Divi / Webflow 这类页面搭建工具默认给锚文字套 span,
其它基金大概率也在悄悄漏链接, 只是没人发现。

### 断点二: 没有"这一页自己就是月报"的判定

`find_archive_v2` (discover2.py:460-644) 的全部判定只有一种形态 —— `_consider()`
(discover2.py:512) 把该页的 `.pdf` 链接清单交 `classify_pdf_links` 判"哪几条是本
基金月报"。`pdf_urls` 为空直接 return, 不再看这页别的任何东西。

月报页本身就是终点, 上面没有可下载的月报文件 (只有 2 条无关的 budget.gov.au PDF)
→ 即使导航跳到了这一页, 照样判 0 个月, 走到步 7 返回 `no_archive`。

### 断点三: 渲染通道必须人工预填成立月份

`webapp/backend/app/routers/ingest.py:379`:
```python
if _is_single_file_html and req.inception_month:
```
- `_is_single_file_html` (ingest.py:375-378): URL 以 `.html/.htm/.csv` 结尾,
  **或** `funds.url_type ∈ {performance_report_html, performance_report_csv}`
- `req.inception_month`: 只能人工在前端表单敲

两者缺一, 就会掉进 else 分支走 `parse_archive_page` (当成 PDF 归档目录页解析) →
0 links → 清空 `confirmed_url` → 下轮重新搜索 → 死循环。

所以即使断点一二修好、`confirmed_url` 存对了, 没有成立月份仍然跑不通。

---

## 3. 修复方案

### 任务 1: 锚文字穿透套壳 (discover2.py)

**改** `_HTML_LINK_RE` 与 `_extract_monthlyish_page_links` (discover2.py:53, 75-109):
- 正则改成非贪婪匹配整段 `<a ...>...</a>` 内部 HTML (含嵌套标签)
- 取到内部 HTML 后剥标签 (`re.sub(r'<[^>]+>', ' ', inner)`) + 压空白, 得到真实锚文字
- 再走原有的 `_MONTHLY_HINTS` 关键词 / `.pdf` 排除 / `/login` 等噪声路径过滤, 逻辑不动

**注意**: `_HTML_LINK_RE` 只被 `_extract_monthlyish_page_links` 一处用到 (已确认),
改它不影响 `_extract_pdf_links` (那个走独立的 `_PDF_HREF_RE`)。改前再 grep 一次确认。

**测试** (`tests/test_discover2.py` 新增一组):
1. 锚文字套 `<span>` + 图标 `<i>` (照抄 §1.2 那段真实 HTML) → 链接能被提取
2. 纯文本锚文字 → 仍能提取 (回归)
3. 套壳但关键词不命中 (如 `<span>Contact Us</span>`) → 不提取
4. 套壳且 href 是 `.pdf` → 仍被排除 (不能混进 PDF 清单, 见 2026-07-29 事故)

**验收**: `_probe_one('https://coolabahcapital.com/coolabah-global-floating-rate-high-yield-fund/')`
返回的 `nav_urls` 里出现 `performance-report-coolabah-global-floating-rate-high-yield-complex-etf`。

---

### 任务 2: `parse_plotly_nav_series` 改用 token 匹配 + 补测试 (plotly_nav.py)

**现状**: `llm_ingest/plotly_nav.py` 是**完全死代码** —— 零调用方、零测试
(全仓 grep 只有它自己)。本次要复活它当判别器, 必须先补测试。

**问题**: 现在按子串匹配 (plotly_nav.py:138 `fund_name_pattern.lower() in name_lower`)。
实测:
```
pattern='Coolabah Global Floating-Rate High Yield Complex'  → ValueError 零匹配
pattern='Global Floating-Rate High Yield Complex'           → 18 点, 0.0s  ✅
```
trace 名是 `Global Floating-Rate High Yield Complex ETF`, 不含发行商前缀 "Coolabah",
所以拿基金全名去子串匹配必然失败。

**改法**: 加 token 子集匹配 (保留原子串路径供既有语义, 但新增匹配模式):
- 双方都按 `fundmonitors.py:84 _name_tokens` 那套切词 (去停用词, 长度 ≥2)
- 追加忽略词: `etf`, `class`, `fund`, `trust`, `complex`? —— **`complex` 不能忽略**,
  它是这支基金的份额类别名, 是区分兄弟基金的关键 token
- 判据: **trace token 集合 ⊆ 基金名 token 集合** (忽略上述类型词后) 即算命中
  - 本例: trace `{global,floating,rate,high,yield,complex}` ⊆
    fund `{coolabah,global,floating,rate,high,yield,complex}` ✅
  - 兄弟基金 `Global Floating-Rate High Yield Fund AI` → 多出 `ai`, 不是子集 ✅ 排除
- **保留** 现有两条硬保护, 一条都不许放松:
  - trace 名含 `benchmark/index/ausbond` → 丢弃
  - 命中 trace 数 `!= 1` → raise (0 条和 >1 条都不许猜)

**性能**: 实测 13.4 MB HTML 上 0.0s, 无需优化。

**测试** (新建 `tests/test_plotly_nav.py`):
1. 手编最小 Plotly HTML (2 trace: 基金 + AusBond 基准) → 只返基金那条, 点数正确
2. trace 名带 `ETF` 后缀 / 基金名带发行商前缀 → token 子集判定命中 (本案例回归)
3. 兄弟基金 trace (多一个不在基金名里的 token) → 不命中
4. 两条 trace 都命中 → raise (不许猜)
5. 零命中 → raise
6. benchmark trace 单独存在 → raise 零命中 (不会把基准当基金)

---

### 任务 3: 发现层加"自身即月报"判定 + 新的指针字段

**加** `discover2._detect_self_report(url, html, fund_name) -> Optional[dict]`:
- 调 `plotly_nav.parse_plotly_nav_series(html, fund_name)` (任务 2 改造后的 token 版)
- 抛异常 (零命中/多命中) → 返回 `None`, 静默放行, 绝不猜
- 成功 → 返回 `{"url": url, "series": [(date, nav), ...],
  "first_ym": "YYYY-MM", "last_ym": "YYYY-MM", "points": N}`
- 点数下限: `< 3` 视为无效 (至少要能算出 2 个月的收益率)

**接入位置** (三处, 都在 HTML 已在手时顺带做, 不额外抓页):
1. `_probe_one` (discover2.py:407-431): HTML 在手, 结果塞进 probe dict 的新键
   `self_report`。注意现在只留 `html_snippet` 前 2000 字符, 判定必须在这里做完
2. 步 5 中转页循环 (discover2.py:587-595): `seed_html` 在手
3. 步 6 导航结果 (discover2.py:616-621): `_next_html` 在手 (目前被丢弃成 `_next_html`,
   要接住)

**判定顺序 (关键)**: **只在 PDF 路径彻底失败后才用**。即在现有步 7
(discover2.py:630 返回 `no_archive`) **之前** 插入一步: 若前面任一页检出了
`self_report`, 返回带自报标记的 `ArchivePointer`。

理由: 有正规月报 PDF 归档的基金, 优先级完全不变; 只有"一份月报 PDF 都判不出来"
时才考虑"这页是不是自己就是数据"。

**ArchivePointer 加字段** (discover.py:43-59):
```python
self_report_url: Optional[str] = None      # 该页自身即月报数据页
self_report_kind: str = ""                 # "performance_report_html"
self_report_first_ym: Optional[str] = None # 序列首月 (成立月推定)
self_report_last_ym: Optional[str] = None
```
`run_discovery` (discover.py:870+) 把这几个字段原样透传到 `DiscoveryReport`
(需同步加字段), 供 ingest 读取。

**测试** (`tests/test_discover2.py`):
1. 页面有 Plotly 序列且无月报 PDF → 返回自报指针, `self_report_kind` 正确
2. 页面**同时**有真月报 PDF 和 Plotly 序列 → 走 PDF 路径 (自报不生效) ← 优先级回归
3. `parse_plotly_nav_series` raise (多 trace) → 不返回自报指针, 走原 `no_archive`
4. 序列点数 < 3 → 不返回自报指针
5. 端到端 (stub 掉网络): 介绍页 → nav 跳月报页 → 检出自报指针

---

### 任务 4: ingest 接住自报指针 + 成立月份自动推

**改** `webapp/backend/app/routers/ingest.py`:

**4a. 发现返回自报指针时** (在 `run_discovery` 调用之后, ingest.py:455-475 那段):
```
若 rep.self_report_url:
    UPDATE funds SET confirmed_url=<self_report_url>,
                     url_type=<self_report_kind>,
                     inception_date=<self_report_first_ym 月末>,
                     inception_assumed=0
    并在**同一个 job 内**直接走单文件多月路径 (不要让用户再点一次"更新")
    月份区间 = self_report_first_ym+1月 .. self_report_last_ym
```
月份区间直接用序列自带的日期, 不再调 `_month_range(inception, 上月)` 去猜
"最新一期是哪个月" —— 序列末尾就是权威的最新月。

**4b. `req.inception_month` 为空时回落读 DB** (ingest.py:379 那个条件):
```
inception_month = req.inception_month or (funds.inception_date 的 YYYY-MM)
```
让**后续每次"更新数据"**都不需要人工再填。

**注意 `_upsert_fund_preserving_existing`** (ingest.py:152-183): 它已经保留
`url_type` 和 `confirmed_url`, 所以 4a 写进去的值在后续 job 里不会被覆盖。
但要确认 `inception_date` 不在 `upsert_fund` 的覆盖列里 (改前 grep 确认)。

**测试** (`tests/test_ingest_*.py`, 建议新建 `tests/test_ingest_self_report.py`):
1. 发现返回自报指针 → DB 里 `confirmed_url` / `url_type` / `inception_date` 三列都写对
2. 同一个 job 内直接进渲染路径 (不是等下一次请求)
3. 第二次跑同一基金: `confirmed_url` 已存 + `req.inception_month` 为空 →
   仍能从 DB 读出成立月份走通 (不掉进 `parse_archive_page` 死循环)
4. 月份区间取自序列 (首月+1 .. 末月), 不是 `_month_range(inception, 上月)`
5. 自报指针为空 (普通 PDF 基金) → 行为完全不变 (回归)

---

## 4. 端到端验收

前置: `.env` 里 `SUB2API_* / GROK_* / TAVILY_API_KEY` 齐全; 后端在跑 (`:8000`)。

```bash
python3 -c "
import sqlite3; c=sqlite3.connect('data/fund_analysis.db')
c.execute(\"UPDATE funds SET confirmed_url='', url_type='archive', inception_date=NULL WHERE fund_id='coolabah_global_floating_rate_high_yield_complex'\")
c.commit()"
```

```bash
curl -s -X POST http://localhost:8000/api/ingest/funds -H "Content-Type: application/json" -d '{"fund_id":"coolabah_global_floating_rate_high_yield_complex","fund_name":"Coolabah Global Floating-Rate High Yield Complex","search_engine":"grok"}'
```

**期望日志链路**:
```
L1 fundmonitors: status=no_fundid
L1 未覆盖, 走 L2 PDF 通路
run_discovery: ...
候选页 .../coolabah-global-floating-rate-high-yield-fund/: PDF 链接 2 条, 中转链接 3 条, 判出 0 个月
跳转: (中转链接) -> .../performance-report-coolabah-global-floating-rate-high-yield-complex-etf
自身即月报: ... 18 点, 2025-01 ~ 2026-06
pre-rendering HTML -> PDF: ...
[N/17] 2025-02 ... ok
done: {'monthly': 17, ...}
```

**期望结果**: 17 个月入库 (2025-02 … 2026-06), 0 download_fail。
数值抽查: 2026-06 月度净收益 ≈ 0.71% (108.18 → 108.95, 与 Commentary 的 net 值一致,
**不是** gross 的 0.80%)。

**再跑一次同一条 curl** (不清 DB): 应直接命中 `confirmed_url` 走渲染, 不再搜索,
且不清空 `confirmed_url`。

---

## 5. 单测运行方式 (RTK 拦截注意)

`python3 -m pytest` 会被 RTK 重写后 spawn 失败, 用:
```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q']))"
```
后端侧:
```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['webapp/backend/tests/','-q']))"
```
基线: 仓库根 `tests/` 目前 414 个用例全绿, 改动后不许有新增失败。

---

## 6. 风险与边界

1. **13.4 MB HTML 进内存**: `_probe_one` 抓这一页会把 13 MB 字符串读进内存,
   并发探测 (`PROBE_CONCURRENCY`) 时可能几十 MB。目前可接受, 若日后候选页更多再说。
   `parse_plotly_nav_series` 本身实测 0.0s, 不是瓶颈。
2. **token 子集判定可能过严**: 若某发行商 trace 名比基金名多带一个词 (如年份/币种),
   会判不命中 → 静默放行走原逻辑 (退化成现状, 不会出错值)。宁可漏, 不可错。
3. **`>1 trace 命中就 raise`**: 同一页挂多个份额类别时会拒绝判定而非挑一个。
   这是刻意的 (2026-07-18 Coolabah 错源 173 月事故的教训), 不许为了"跑通"放松。
4. **不碰 `extract_html.py` 的字节窗口老路**: 已删, 不复活 (见该模块说明)。
5. **不碰 Grok 提示词**: Grok 给入口页是合法行为, 靠导航兜住, 不去调教模型多给一个字段。

---

## 7. 明确不做

- 不改 `classify_pdf_links` 的判定逻辑 (它工作正常, 这次问题不在它)
- 不把 `parse_plotly_nav_series` 的结果**直接入库** —— 仍走
  `render_html_to_pdf` → PDF 提取 → 两道闸。序列只当**判别器**和**月份区间来源**,
  不当数据源 (绕过两道闸就绕过了反捏造防线)
- 不给前端加"这是网页型月报"的手动勾选框 (自动识别就是为了消灭这个人工步骤)
- 不处理 Coolabah 其它兄弟基金 (本次只验这一支; 通用性靠任务 1/2/3 自然覆盖)
