# skills 重构计划:提示词驱动 -> 代码驱动(2026-07-14)

> **本文件是 compact 后继续执行的唯一依据,自包含。** 执行前通读第 3 节修正清单(不可违反)+ 第 10 节瘦身安全阀(M5 必须先 inventory + 映射表再动 md)+ 第 11 节执行纪律,再按第 8 节顺序执行。

## 0. 状态
- 基线已 push:`cdddabd` on main(stealthy_fetch 加固版,重构起点)
- 工作区:`/Users/chong/Desktop/fixed_fund_analysis/skills`
- Python 3.9.6,`Optional[X]`(非 PEP 604),`python3`/`pip3`
- DB:`FUND_DB_PATH` 环境变量,默认 `<仓库根>/data/fund_analysis.db`
- RTK 拦截 pytest:用 `python3 -c "import pytest; pytest.main([...])"` 绕过(memory `rtk-pytest-interception`)
- /tmp 禁放 .py(inspect 遮蔽,memory `tmp-inspect-shadowing`);脚本在 skills/ 跑

## 1. 根因
- 当前 skills 是"提示词程序":`add_fixed_fund.md`(23.8K)+ `update_fixed_fund.md`(5.6K)+ `skills/CLAUDE.md` 七八章 + 7 个 memory 条目,数千字规则让 LLM 当解释器执行搜索决策 -> 依赖上下文/agent 能力 -> 产出不稳(平均 15min,网页版 30s 能定位)
- 代码(strategies/ingest)反而是配角,等 LLM 喂料
- **入库层(ingest.py)+ 验证层(extract.py)早已代码化且稳定**;唯一还靠 LLM 的是"定位 + 抓归档页 HTML"
- extract.py 能力已完整:parse_pdf_text / extract_commentary_return / extract_pdf_links_from_archive / download_and_extract_parallel / gate_check / parse_html_monthly_table / parse_plotly_nav_series / extract_bentham_net_return
- ingest.py 三流水线:add_fund(PDF 归档) / add_fund_from_html_table / add_fund_from_plotly_html,含 gate_check + consistency_check + 并发下载 + NAV 重算

## 2. 大方向(已批准)
- 提示词程序 -> 真程序(python),LLM 从**解释器**降为**调用者 + 兜底**
- **LLM 只做**:① 定位(搜索找已验证 URL,30s,无免费代码搜索 API)② 兜底(被代码点名补料 / 异常)
- **代码做**:抓取 + 解析 + 判口径 + 验证 + 缺口 + 入库(确定性,同输入同输出)
- **取舍**:代码驱动遇新格式硬失败报错(要人补提取器),不靠 LLM 临场灵活提取--符合 CLAUDE.md 不捏造/失败报错。LLM 临场提取正是幻觉回填和不稳来源
- **瘦身本质验收线**:新 md 只含"调用约定 + 铁律 + 新流程说明",**零决策逻辑**。决策逻辑一条都不许留在 md

## 3. 修正清单(全部已批准,不可违反)

### 3.1 上一轮四条原则修正
1. **PDD 6 策略遍历不删,降级为代码内 fallback 链**(按 gap_set 触发,LLM 被点名补料)。砍的是"无条件穷尽",不是策略清单。Macquarie 型(官网无归档,L1 只能给 latest 静态链接)必须有自动退化路径
2. **LLM 兜底输出过同一套闸门 + 进 pending_review,永不直通 ingest**。兜底=帮人工减负一步,非绕过闸门
3. **L1 产出 = 已验证 URL**(HTTP 200 + content-type + 首屏摘要),代码端独立复核。"我找到了X"不作数,"我 fetch 过X 返回Y"才作数
4. **确定性只对逻辑不对环境**。网站改版/WAF 变是常态。硬失败错误带足上下文(环节/拿到/期望)

### 3.2 六条修正(集合差驱动)
1. **全局删 target=36**。爬取层不存在"够了"阈值。36 是下游去平滑准入条件,属分析层,爬取层禁知
2. **expected_range 参照系**:inception_date 步骤①落盘;无精确日 -> 全链最早可得月作下界标 `inception_assumed=True`;latest 已发布月(距今 ≤2 自然月算正常滞后,不算缺口)
3. **fallback 链集合差驱动**:obtained_months(去重 (year,month) 并集)、gap_set=expected−obtained;每级输入 gap_set 只补洞不重抓;榨干(新增0 或连3失败)才降级;gap 空早停
4. **缺口非失败**:gap 入 confirmed_gaps 表,仅 obtained 空集才 human_intervention;update 侧复查 confirmed_gaps
5. **DiscoveryReport 删 coverage 三态**,改 obtained/gaps/per_level_contribution
6. **(year,month) 去重严格**:整链裁决建立在集合运算上,月解析错一个 gap 错一个;月数=去重集合大小,非链接数

### 3.3 三补一确认(实现层)
- **补1**:L2 CDX 单月快照上限 = gap_set 每月最多试时间戳最近 3 个快照,试完即该月 L2 不可得。级别切换靠连3失败/新增0,单月成本靠此上限,并行
- **补2**:"缺口零容忍"闸门 = **提取诚实性约束**(单次提取不许静默跳月,声称 [a,b] 必须出全集或显式报缺月)。管提取过程诚实;confirmed_gaps 管世界可得性(结果记录),不冲突
- **补3**:下界向更早**单向收敛**(禁后缩 = 禁静默删缺口);收敛后 expected_range 扩大、gap_set 立即重算,新暴露早期月入后续补洞;inception_assumed 基金 update 侧顺带低成本重探下界(只查 CDX 更早快照)
- **确认 |r|<0.5**:超限 -> 拒收进 **pending_review 人工裁决,不丢弃**(CLAUDE.md 第五条)

## 4. 先行文档 1:代码 fallback 链(集合差驱动)

**参照系 expected_range**(链开始前确立,下界可动态收敛):
- 下界 = `max(inception_date, 全链最早可得月)`;inception 无精确日 -> 下界=最早可得月 + `inception_assumed=True`
- 上界 = 最近已发布报告月(距今 ≤2 自然月算正常滞后,**不计入 gap**)
- expected_range = 上下界间所有 (year,month)

**状态变量(跨级累计)**:obtained_months(去重 (year,month) 并集)、gap_set = expected_range − obtained_months

**每级任务**:输入 = 当前 gap_set(缺哪些月),**只补洞,不重抓已有月**。

| 级 | probe | 输入 | 补洞逻辑 | 降级触发 | 贡献度量 |
|----|-------|------|---------|---------|---------|
| L0 | local_cache | db_path | DB 已有月份并集 | 新增=0 且 gap 非空 | L0: n |
| L1 | official_evergreen | **已验证 URL+fetch 证据**(3.1.3) | 单PDF逐月表 / 单月Commentary+归档页PDF链接对应月份,补 gap | 新增=0 或连3失败 | L1: n |
| L2 | wayback_cdx | issuer_domain(代码从L1推) | CDX快照对应月份补 gap 中洞;**单月最多试最近3快照**(补1) | 新增=0 或连3失败 | L2: n |
| L3 | fundmonitors | FundID+AccCode(代码点名->LLM补料) | AJAX逐月表月份补 gap | 新增=0 或连3失败 | L3: n |
| 终 | 收尾 | - | gap 仍非空 -> **正常结束** | - | - |

**触发/停止裁决**:降级=本级新增==0 且 gap 非空;补充降级=连3失败;早停=gap 空;**无 target 阈值**。

**L4 重定义**:全链跑完 gap 仍非空 -> **正常结束**(obtained 入 monthly_returns,gap 写 confirmed_gaps);仅 obtained==∅ 才 human_intervention。

## 5. 先行文档 2:LLM 兜底通道验证闸门清单

两类兜底:① 定位兜底(L1 已验证 URL)② 提取兜底(代码提取器覆盖不到,LLM 读 PDF 提收益)。**两者输出永不直通 ingest**。

| 闸门 | 代码提取器路径 | LLM 兜底路径 |
|------|--------------|-------------|
| 结构约束 | dataclass 内置 | 强制 JSON Schema 校验,不符拒收 |
| source_quote | 代码带原文片段 | **必须附原文 quote** |
| 定位 fetch 证据 | - | HTTP 200+content-type+首屏摘要 |
| 复利 gate_check | 过 | **过同一函数** |
| 提取诚实性(补2) | 单次提取不许静默跳月 | 同 |
| 字段类型(\|r\|<0.5) | 过(超限进 pending_review) | 过 |
| **入库目的地** | monthly_returns(过 gate 直入) | **pending_review 表** |
| 终决 | 自动 | 人工 promote 后入 monthly_returns |

**两目的地区分**:`pending_review`=值待审(含 |r|<0.5 超限);`confirmed_gaps`=确无此月(穷尽后正常产出)。互不混淆。

## 6. 完整实现计划(M1-M7)

### M1. strategies.py 重写(集合差驱动)
- DiscoveryReport 字段:删 `coverage`/`exhausted`/`premature_exit`;改 `obtained`/`gaps`/`per_level_contribution`/`inception_date`/`inception_assumed`/`human_intervention_needed`(仅空集 True)/`unparseable_links`
- probe 签名:`probe(fund_info, gap_set) -> ProbeResult(new_months:set, pending_input:Optional[dict], failures:int, evidence)`
- run_discovery(fund_info, expected_range):L0->L3 按序;每级 obtained|=new;gap_set=expected−obtained;记 per_level;降级=new==0 或连3失败;早停=gap 空;L3 缺 FundID 返 pending_input 挂起(支持断点续跑)
- L2 CDX probe 内置补1 上限:每月最近3快照逐试
- 删 distributions/third_party_rolling 占位

### M2. db.py schema
- funds 加 `inception_date TEXT`、`inception_assumed INTEGER default 0`(幂等迁移)
- 新增 `confirmed_gaps` 表:`(id PK, fund_id, missing_month(YYYY-MM), exhausted_levels, checked_at, UNIQUE(fund_id,missing_month))`
- 新增 `pending_review` 表:`(id PK, fund_id, date, net_return, source_quote, extract_method(code/llm), gate_result, review_state(pending/approved/rejected), review_reason, created_at)`
- 新增 `promote_pending(conn, review_id)`
- 写操作仍受 `_require_write_token` 保护

### M3. extract.py 月份解析严格化(★重点2)
- extract_pdf_links_from_archive 等返回 (year,month) 去重集合,月数=集合大小
- **解析失败处理(★)**:解析不出的链接 -> 不计 obtained,进 `unparseable_links` 日志(url/raw_text/reason),写 DiscoveryReport,**不静默消失**
- extract_commentary_return 等返回值附带 source_quote

### M4. ingest.py 入库分流
- add_fund 接 DiscoveryReport:obtained 过 gate_check -> monthly_returns;gaps -> confirmed_gaps
- LLM 兜底提取入口:records 进 pending_review(过同一 gate_check)
- |r|<0.5 超限 -> pending_review(review_reason="abs_return_exceeds_threshold")
- CLI 加 `--url <已验证URL>`(代码 requests/curl 抓归档页;失败退回 LLM stealthy_fetch 存文件走 --archive-html)
- CLI 加 `--inception-date` + `--inception-assumed`

### M5. 提示词瘦身(详见第 10 节安全阀,必须先 inventory + 映射表再动)
- add_fixed_fund.md:23.8K -> **≤80 行/≤3000 字**
- update_fixed_fund.md:5.6K -> 同上限,加 confirmed_gaps 复查 + inception_assumed 重探
- skills/CLAUDE.md:七章八章旧规则按映射表删,一-六章保留

### M6. update 侧复查 + pending_review 滞留报告
- update_fixed_fund:每月刷新对 confirmed_gaps 每月轻量复查(CDX/fundmonitors 该月是否新可得),有收获补录 monthly_returns + 移出 confirmed_gaps
- inception_assumed 基金:复查顺带低成本重探下界(补3,只查 CDX 更早快照)
- **pending_review 滞留报告(改3 落地)**:update 每次跑完,输出 `pending_review` 中 `review_state='pending'` 且 `created_at` 滞留 >14 天的条目清单,打进最终报告(防人工审核队列变静默坟场)

### M7. 测试 + grep 验收(详见 10.4)
- test_strategies/test_extract/test_db/test_ingest
- **grep 机械化验收**(10.4):对新 md + CLAUDE.md grep 已删概念关键词,命中(旧决策逻辑上下文)即失败

## 7. 实现重点两处(★钉死)

### ★重点1:strategies.py gap_set 重算触发位置(补3)
run_discovery 主循环每级 probe 返回后:
1. 常规:obtained|=new_months;gap_set=expected−obtained
2. **下界收敛检测**:min(new_months) < current_lower_bound -> 下界单向前移 -> expected_range 扩展 -> **gap_set 重算**(新暴露早期月入队)
3. 重算必须在"记 per_level 之前、下一级 probe 之前"
4. 收敛只单向向更早,禁后缩

### ★重点2:extract.py 月份解析失败处理
解析失败链接进 unparseable_links 日志(url/raw_text/reason),写 DiscoveryReport,不计 obtained,**不静默消失**。

## 8. 执行顺序
1. **M2 db.py schema**(表先建)-- 先 test_db
2. **M3 extract.py 月份严格化** -- 先 test_extract
3. **M1 strategies.py 重写** -- 先 test_strategies
4. **M4 ingest.py 入库分流** -- 先 test_ingest
5. **M5 提示词瘦身**(先 inventory + 映射表,第 10 节)
6. **M6 update 侧复查 + 滞留报告**
7. **M7 测试 + grep 验收**
8. **端到端验证**(见第 11 节测试纪律:pipeline 测试与定位能力测试分开)
- 每模块 TDD -> pytest(`python3 -c "import pytest; pytest.main([...])"`) -> commit(main,Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>)
- 完成即 commit 不攒批(memory `task-tracker-sync-immediately`)

## 9. 不变约束(CLAUDE.md 铁律,重构中不可违反)
- 数据完整性最高优先级:不捏造、提取诚实性(补2)、异常值保留交人工
- 提取层只做纯文本到数字映射,禁 backfill/forward-fill
- 序列起点=第一份真实研报日期
- ANTI-FABRICATION GUARD
- skills 只写 funds + monthly_returns(+ confirmed_gaps / pending_review),不算指标/不检测异常/不更新 RBA
- 入库后提示 webapp `POST /api/funds/{fund_id}/recompute`
- 搜索/抓取主会话执行不派子 agent(memory `search-no-subagent-main-session`,新架构仍适用)

---

## 10. M5 瘦身安全阀(必须先做,再动 md)

### 10.1 Inventory:所有含旧规则的文件/memory 清单 + 处置

| # | 文件/memory | 含旧规则情况 | 处置 |
|---|------------|------------|------|
| 1 | `skills/.claude/skills/add_fixed_fund.md` (23.8K) | 主重灾区:archive断言/工具隔离/并行度/止损/探测纪律/4min/分类路由/PDD遍历/选源优先级/抓取工具分流/硬约束9条 | 重写瘦身(≤80行/≤3000字),按 10.2 映射表逐条迁删 |
| 2 | `skills/.claude/skills/update_fixed_fund.md` (5.6K) | fetch_method分流/LLM提取新月/缺口零容忍硬停/硬约束7条/末尾"子agent委派"(与memory冲突)/**缺confirmed_gaps复查+滞留报告** | 重写:代码增量+confirmed_gaps复查+inception重探+pending_review滞留报告;删"子agent委派" |
| 3 | `skills/CLAUDE.md` 七章(数据源优先级) | archive断言/工具隔离/并行度/机械化止损/探测纪律/4min/分类路由/slug两阶段/数据源1-7条 | 七章按映射表大幅删(进代码),留"官网优先"原则一句 |
| 4 | `skills/CLAUDE.md` 八章(PDD遍历) | STRATEGY_LIST无脑遍历/DiscoveryReport三态/premature_exit | 改写为"代码fallback链"简述(对齐修正1/5) |
| 5 | `skills/CLAUDE.md` 一-六章 | 数据完整性/职责边界/环境/反幻觉 | **保留**(铁律) |
| 6 | memory `datasource-priority-cognitive-trap` | 官网优先+禁复用fundmonitors假设+付费墙跳过+穷尽所有下载入口 | 改写:删"穷尽所有下载入口/逐月表确认"(进代码L1),留"官网优先+付费墙跳过"原则一句 |
| 7 | memory `discovery-workflow-single-month-default` | 单月默认+4min预算+探测纪律(禁构造URL/禁404/禁CDX过度) | 大部分进代码(M1 L1 probe/补1/修正3)。改写:删4min/探测纪律(已进代码),留"单月PDF常态"作reference一句 |
| 8 | memory `coolabahcapital-mcp-proxy-block` | MCP被拦curl兜底/工具分流/PDF无逐月表/Commentary gross-net/Plotly NAV源/越权教训 | 改写:删"工具分流规则"(进代码M4),保留特例数据源(Plotly/CSV)+口径+越权教训作reference |
| 9 | memory `fundmonitors-full-profile-monthly-table` | AJAX逐月表源+FundID查询+stealthy_fetch JS必须 | 改写:删"stealthy_fetch必须"(代码curl抓AJAX),留"AJAX逐月表源+FundID"作L3 reference |
| 10 | memory `ingest-threshold-8months-not-36` | 入库>8月,36是去平滑标准 | **保留**,与修正1一致(爬取层无36,入库层>8月)。微调措辞对齐 |
| 11 | memory `macquarie-no-pdf-archive-cum-price-csv` | Macquarie特例:CSV cum-ex重建 | **保留**作reference(human_intervention时参考方案+可能专用ingest命令) |
| 12 | memory `search-no-subagent-main-session` | 搜索主会话执行不派子agent | **保留**(新架构仍适用),删旧"步骤1/2/3"流程描述 |
| 13 | memory `bentham-commentary-net-vs-gross` | Bentham提取口径 | **保留**(提取器reference) |
| 14 | memory `stake-performance-table-pitfall` | Stake口径陷阱 | **保留** |
| 15 | memory `cc-proxy-no-websearch`/`rtk-pytest-interception`/`tmp-inspect-shadowing`/`task-tracker-sync-immediately`/`llm-belongs-to-skills-side` | 环境/工具约束 | **保留**(与新架构无关,仍有效) |
| 16 | `docs/superpowers/plans/*` + `specs/*` | 历史归档(已完成工作记录) | **不清理**(归档性质,新架构不读执行)。确认新md不引用它们 |
| 17 | `MEMORY.md` 索引 | memory索引 | 改写/删除的memory条目(6,7,8,9)索引行同步更新 |

### 10.2 三列映射表:旧规则 -> 处置 -> 去处(找不到去处进待迁移,不许删)

| 旧规则 | 处置 | 去处/依据 |
|--------|------|----------|
| archive入口断言(≥6日期链接) | 删 | 集合差驱动(M3 月数=去重集合大小) |
| 4min墙钟预算 | 删 | 补1(L2单月3快照上限)+榨干降级 |
| 分类路由决策(tier1/tier2分支) | 删 | fallback链统一(L0-L3不分ASX,按gap退化) |
| ASX上市基金定位捷径(asx-announcements/listcorp) | **保留**(reference) | 新md步骤③一句reference:"上市基金归档页通常在官网investor reports或ASX公告聚合站(listcorp等)"。非分支逻辑,服务LLM定位唯一决策点 |
| 工具隔离(reader-mode禁提链接/stealthy主力) | 删 | 代码requests/curl抓归档页(M4),reader-mode不再用 |
| 并行度(≥4角度/curl2路/stealthy bulk5-6) | 删 | 代码内requests/curl(M4),无LLM并行决策 |
| 机械化止损(换工具≠换目标/3条切换链) | 删 | 代码fallback链自动切换(M1) |
| 探测纪律-禁构造URL猜slug | 删 | 修正3(L1已验证URL契约,代码独立复核) |
| 探测纪律-禁404诊断 | 删 | 代码L2 CDX失败处理 |
| 探测纪律-单月确认不深挖CDX | 删 | 代码L2单月3快照上限(补1) |
| 单月默认+并行查单月/逐月 | 删 | 代码L1 probe自动判Commentary/逐月表(M1) |
| PDD 6策略无脑遍历 | 改 | 代码fallback链按gap_set触发(M1,修正1) |
| DiscoveryReport coverage full/partial/none | 删 | obtained/gaps/per_level_contribution(修正5) |
| premature_exit/exhausted字段 | 删 | 集合差裁决(修正5) |
| 数据源优先级1-7条 | 部分删 | 顺序进fallback链(L0-L3);付费墙跳过进L3 probe;必须下载确认进代码L1 |
| 子agent委派(add/update) | 删 | 主会话执行(memory),代码ingest主会话跑 |
| 抓取工具分流(归档stealthy/PDF curl/兜底互切) | 删 | 代码requests/curl统一抓(M4) |
| 硬约束9条(Commentary优先/负号/gate/并发/inspect/起点/失败隔离/必须下载/口径核对) | 部分保留 | 提取约束(Commentary优先/负号/口径)进extract.py代码;gate/并发/失败隔离进ingest;inspect/起点保留md铁律 |
| 份额类口径(shareclass一致) | 保留 | consistency_check代码已有 |
| update缺口零容忍(硬停) | 改 | confirmed_gaps(修正4)+提取诚实性(补2) |
| **待迁移**:Macquarie CSV cum-ex重建 | 待定 | 特例,不在fallback链。进human_intervention参考 + 评估是否加add-from-csv命令(实现时定) |
| **待迁移**:Coolabah Plotly NAV源 | 保留 | add_fund_from_plotly_html已有,reference保留 |

### 10.3 新 md 目标骨架 + 体积上限

**add_fixed_fund.md 骨架(目标 ≤80 行/≤3000 字,零决策逻辑)**:
```
# /add_fixed_fund <基金名或标识>
## 职责边界(仅入库原始数据,不算指标;一句话)
## 输入
## 环境前提(skills/工作区,DB路径,依赖)
## 工作流
### 1. 确认基金信息 + inception_date 落盘(查官网/招股书/ASX;无精确日标 inception_assumed=True)
### 2. 检查已注册(查funds表)
### 3. 定位(已验证URL+fetch证据:HTTP200+content-type+首屏摘要)-- LLM唯一决策点
     定位reference(非分支逻辑):上市基金(有ASX代码)归档页通常在官网investor reports或ASX公告聚合站(listcorp等)
### 4. 跑 ingest(代码fallback链全自动:ingest.py add --url <已验证URL> --inception-date)
### 5. pending_input 补料重入(代码点名时,如fundmonitors FundID;LLM补料后续跑)
### 6. 输出(obtained月数/gaps入confirmed_gaps/pending_review提示)
## 数据完整性铁律(不捏造/提取诚实性/异常值保留/无幻觉回填;引用CLAUDE.md)
## 人工通道
### pending_review审核入口(promote_pending;LLM兜底提取/|r|<0.5超限值待人工确认)
### confirmed_gaps说明(缺口非失败,update侧复查)
```

**update_fixed_fund.md 骨架(目标 ≤80 行/≤3000 字)**:
```
# /update_fixed_fund <基金ID>
## 职责边界
## 工作流
### 1. 读基金配置 + 现有月份 + confirmed_gaps
### 2. 代码增量:fallback链复查gap_set(已得月之外)+ confirmed_gaps每月复查
### 3. inception_assumed重探下界(若标记,只查CDX更早快照)
### 4. upsert新月 + 更新confirmed_gaps(补录/移除)
### 5. 输出 + pending_review滞留报告(review_state='pending'且>14天的条目清单)
## 数据完整性铁律
```

**新流程调用约定(必须写进新md,否则缺调用入口)**:
- inception_date 落盘操作说明(步骤①)
- pending_input 挂起时 LLM 怎么补料重入(步骤⑤:代码返回缺什么输入,LLM 补料后重跑 ingest)
- pending_review 人工审核入口(promote_pending 命令/操作)

### 10.4 grep 机械化验收(M7 补,shell 检查非 pytest)

对新 `add_fixed_fund.md` + `update_fixed_fund.md` + `skills/CLAUDE.md` grep:

**绝对不应出现(命中即验收失败)**:
`4min`、`4分钟`、`分钟预算`、`≥6`、`>=6`、`archive断言`、`archive入口`、`并行度`、`角度数`、`2路封顶`、`机械化止损`、`换工具`、`探测纪律`、`分类路由`、`tier1`、`tier2`、`slug两阶段`、`reader-mode禁`、`premature_exit`、`exhausted`、`coverage`、`target=36`、`子agent委派`、`穷尽式`

**需上下文判断(命中后人工判,属旧决策逻辑则失败)**:
`stealthy_fetch`(允许在"curl失败时LLM用stealthy_fetch兜底"上下文出现,禁在"工具选择规则"上下文)、`穷尽`(允许"穷尽后入confirmed_gaps",禁"无条件穷尽")

grep 命令示例:
```bash
for f in skills/.claude/skills/add_fixed_fund.md skills/.claude/skills/update_fixed_fund.md skills/CLAUDE.md; do
  echo "=== $f ==="; grep -nE "4min|4分钟|分钟预算|≥6|>=6|archive断言|archive入口|并行度|角度数|2路封顶|机械化止损|换工具|探测纪律|分类路由|tier1|tier2|slug两阶段|premature_exit|exhausted|target=36|子agent委派|穷尽式" "$f" || echo "clean"
done
```
命中(非 "clean")即验收失败,必须清到 clean。成本 1 分钟,防角落残留半句旧逻辑被 LLM 下次读到重新执行。

---

## 11. 执行纪律(执行中遵守)

### 11.1 待迁移规则:停下问,不现场发明
执行中(尤其 M5 瘦身、M1-M4 实现)若出现"某条旧规则找不到代码去处、又不确定过时"的情况:按 10.2 映射表规则进"待迁移"列表,**停下来问用户**,不为了赶进度现场发明处置方式。映射表是安全阀,不是装饰。

### 11.2 端到端测试:两种目的分开,别混一轮下结论
- **pipeline 测试**(验证代码 fallback 链/入库分流/gate):可用 MXT/MOT(单PDF逐月表,L1早停)。但这两只在 memory/skill 文档里已有痕迹,**不能兼作"LLM定位能力"公平测试**。
- **LLM 定位能力测试**(验证新md步骤③定位+已验证URL契约):用之前留的干净基金 **Daintree / GCI / Kapstream**(无 fund 专属 memory 记录,全盲测)。
- 两种测试目的分开,不在同一轮里混着下结论。pipeline 通不代表定位能力达标,反之亦然。
