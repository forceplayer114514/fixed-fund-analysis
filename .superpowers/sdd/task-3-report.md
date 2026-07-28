# Task 3 Report: 【漏洞 10.6】删除「搜索结果里的 .pdf 直接当月报」捷径

## 实现内容

1. 在 `tests/test_discover.py` 末尾（`if __name__ == "__main__":` 之前）新增
   `TestSearchLayerDoesNotYieldPdfLinks` 测试类，内容与 brief 给出的代码逐字一致。
2. 在 `llm_ingest/discover.py` 的 `find_archive_via_search` 兜底分支
   （原 422-447 行）按 brief Step 3 指定的 diff 删除「优先 PDF」for 循环，
   并把 `if not latest_pdf_url and final_domain:` 改为 `if final_domain:`，
   附上 brief 给的注释说明。
3. **额外必要修复（超出 brief 字面 diff，见下方「与 brief 的偏差」）**：
   在「其次挑同域页面」的 for 循环内新增一行 `.pdf` 后缀过滤
   （`if s.lower().endswith(".pdf"): continue`）。

## RED

命令：
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py::TestSearchLayerDoesNotYieldPdfLinks','-v']))"
```

结果：`FAILED` —— 断言消息为
`AssertionError: 搜索结果里的第三方 PDF 被直接当成月报采纳了 -- PDF 链接只能来自真实抓取的页面 HTML`，
`ptr.latest_pdf_url` 等于三方 PDF `https://www.pricefinancial.com.au/.../Gryphon-GCI-Jun-2026.pdf`。
与预期一致：此时代码里「优先 PDF」捷径尚未删除，测试按预期先失败。

## GREEN

命令：
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py', '-v']))"
```

结果：`54 passed, 6 warnings in 0.10s`。新测试通过，`test_discover.py` 其余 53 个既有测试无新增失败。

**中间过程说明**：只应用 brief Step 3 指定的字面 diff 后，重新跑该测试**仍然 FAILED**——
`ptr.latest_pdf_url` 依旧等于三方 PDF。详见下方偏差说明。追加 `.pdf` 后缀过滤后才转 GREEN。

## Files changed

- `llm_ingest/discover.py`（`find_archive_via_search` 内的兜底分支，约 419-455 行区间）
- `tests/test_discover.py`（新增 `TestSearchLayerDoesNotYieldPdfLinks` 测试类）

Commit: `4d9c82a` - `fix(discover): 删除搜索结果直接当 PDF 的捷径 (Spec G 10.6)`

## 与 brief 的偏差（需要重点关注）

Brief 明确要求 `if final_domain:` 内部的 `fund_tokens`/`best_score`/`best_url`
逻辑「保持原样不动」。但严格按 brief 字面 diff 打完补丁后，用 brief 给出的
**同一个测试**验证，测试仍然 RED：

**根因**：`_pick_issuer_domain(real_sources, issuer, fund_name)` 在基金关键词
未命中任何 host 时，会回退为「sources 里第一个未被排除域名的 host」
（`llm_ingest/discover.py:284-307`，第二个 for 循环）。用 brief 测试里的
`sources = [third_party_pdf, "https://gcapinvest.com/our-lit"]` 实测：

```
_pick_issuer_domain(sources, "Gryphon Capital", "Gryphon Capital Income Trust")
# -> "https://pricefinancial.com.au"
```

即 `final_domain` 被误判为三方站域名。随后「其次挑同域下最高分页面」的循环
只用 `_same_host(s, final_domain)` 筛选候选，**从未按扩展名过滤**——它能安全
省略这层过滤，完全是因为在删除前，「优先 PDF」的第一段循环总会先把 real_sources
里所有 `.pdf` 提前摘走，使得这段「其次」分支永远不会看到任何 `.pdf` URL。
brief 删除第一段循环后，这个隐含前提被打破：本例中三方 PDF 是唯一落在
`pricefinancial.com.au` 这个（被误判的）同域下的候选，于是它又被当作
"同域最佳页面" 选中——用另一条路径复现了同一个漏洞（三方转贴 PDF 被当月报采纳）。

这与本 task 明确陈述的规矩直接冲突：「PDF 链接一律只能来自真实抓取的页面 HTML」。
且这不是测试构造的巧合边缘情况——brief 本身举的真实事故例子（Tavily 搜
"Gryphon Capital Income Trust"，pricefinancial.com.au 排首位）中，
`_pick_issuer_domain` 的 token 集合同样只剩 `"gryphon"`，而真实 issuer 域名
`gcapinvest.com` 同样不含 "gryphon"，所以真实场景下 `_pick_issuer_domain`
误判域名、进而让「同域页面」分支重新暴露同一漏洞的可能性是真实存在的，
不是测试假设的极端情况。

**处理方式**：在「其次挑同域页面」循环内追加一行 `.pdf` 后缀过滤后跳过该候选，
不改动 `fund_tokens`/`best_score`/`best_url` 的打分算法本体，只是让该循环真正
落实其注释所声称的「挑页面不挑 PDF」。这是 in-scope 的最小必要修复：
不修复的话，Task 3 的验收测试（brief 亲自给出的那个）实际跑不 GREEN，
且该 task 要堵的漏洞并未真正堵住，只是换了个触发路径。

`_pick_issuer_domain` 本身「关键词未命中就回退选第一个源域名」的启发式
仍然存在设计缺陷（可能选中任意三方站作为"确认域名"），但这已超出 Task 3
（漏洞 10.6：删除搜索结果里的 PDF 捷径）的范围，值得后续单独跟进，
本次未改动 `_pick_issuer_domain` 本体。

## Self-review

- 「优先 PDF」for 循环已完整删除，确认 grep 不再命中该段代码。
- `if not latest_pdf_url and final_domain:` 已按 brief 改为 `if final_domain:`。
- `fund_tokens`/`best_score`/`best_url` 算法本体未被重构，只新增一行提前
  `continue` 的过滤条件，行为上是纯粹的收窄（收紧漏洞面），不改变其余候选的
  打分/选择顺序。
- 新测试确实经由 `find_archive_via_search` 真实函数路径执行（mock 了
  `multi_query_search` 和 `client.messages`，其余走真实代码），不是 mock 到
  trivial pass；且构造的第三方 URL/host 与 brief 描述的真实事故一致，
  能验证阶段 A 之后的兜底分支不会把它当作月报采纳。
- 未改动 `llm_ingest/discover.py`、`tests/test_discover.py` 之外的任何文件。
- 全量 `tests/test_discover.py`（54 项）通过，未发现新增失败。
- 未跑全仓库测试套件（brief 未要求；只验证了 `test_discover.py`，且已确认
  `find_archive_via_search` 无其他调用方的测试文件，`grep -rln "find_archive_via_search"`
  只命中 `llm_ingest/discover.py` 和 `tests/test_discover.py` 本身）。

## Concerns

- 已在上方详细说明的「与 brief 的偏差」需要人工确认是否认可这一额外修复；
  如认为不应超出字面 diff，需要决定是接受当前 GREEN 方案，还是改为在
  brief 层面重新设计（例如显式修复 `_pick_issuer_domain` 的回退逻辑）。
  本次选择了影响面最小、最直接对齐 task 目标（"PDF 链接只能来自真实抓取页面"）
  的方案。
- `_pick_issuer_domain` 的「关键词未命中即回退选第一个源域名」缺陷未修复，
  留作后续任务的潜在候选（不在本 task 范围内，未做任何改动）。
