# Task 5: 优化 URL 探测 discover_source.py 与测试

## Files
- Modify: `scripts/discover_source.py`
- Modify: `tests/test_discover_source.py`

## Requirements
1. **超时时间缩短**：
   - 缩短 `scripts/discover_source.py` 内网络请求默认超时时间至 6 秒（包括 `verify_url_async` 中的 `timeout=6`、`fetch_yahoo_async` 中的 `timeout=6`、`fetch_ddg_async` 中的 `timeout=6`、`parse_sitemap` 中的 `timeout=6`，以及 `extract_links_from_page` / `check_wayback_machine` / `requests.head` 中对应的超时时间）。

2. **限制匹配与爬取深度**：
   - 在 `parse_sitemap` 中过滤路径深度（斜杠数）大于 5 的 URL，且最多返回前 15 个链接。
   - 对 `filtered_candidates` 截取前 15 个链接。
   - 对 `all_truncated` 截取前 15 个链接。
   - 对 Sitemap 校验链接列表 `sitemap_urls` 截取前 15 个链接。
   - 对爬取 roots `parent_roots` 限制最高 5 个，收集到的 `all_sub_links` 限制最高 15 个。

3. **异步并发验证**：
   - 在 Step 4 中，将 sitemap_urls 的串行 verify 循环，修改为并发 `verify_candidates_async` 的校验。
   - 将 all_sub_links 的串行 verify 循环，修改为并发 `verify_candidates_async` 的校验。
   - 对以上两个并发任务，使用 `asyncio.wait_for` 并传入剩余超时时间限制其最大生命期。

4. **全局 45 秒超时机制**：
   - 在主动探测开始处记录 `start_time` 并设置 `GLOBAL_TIMEOUT_SECS = 45`。
   - 新增 `check_timeout()` 并于主要步骤（搜索后、截断后、爬取前后）前调用校验以保证超时快速失败。

5. **编写测试**：
   - 补充 `tests/test_discover_source.py` 测试：
     - 测试 `parse_sitemap` 的深度限制过滤（深度 > 5 级的 URL 应被过滤）。
     - 测试 `verify_candidates_async` 能否正确异步并发筛选出唯一的成功链接。
