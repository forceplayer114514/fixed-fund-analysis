# 固收对比计算流水线主动探测性能优化计划

## Context
在注册新基金时，`discover_source.py` 中由于在 sitemap 与 parent page 解析循环中使用了同步阻塞式的 `verify_url` 请求，且没有控制总体的候选链接预算与请求超时时长，导致整个主动探测流程最高可能挂起多达 8 分钟以上。
此计划旨在将这些串行请求升级为高并发的异步探测逻辑，并对请求数量、深度以及总时间实施硬性的保护预算限制，使探测逻辑能够在 45 秒内快速返回或安全超时终止。

## Critical Files to Modify
1. `scripts/discover_source.py`: 优化 HTTP 客户端超时时长，引入候选链接数量限制，将 sitemap 及 parent page 串行循环变更为并发异步校验，引入全局 45 秒超时与快速终止机制。
2. `tests/test_discover_source.py`: 编写测试用例以验证 sitemap 的深度路径过滤和并发 `verify_candidates_async` 的校验逻辑。

## Proposed Changes

### 1. 超时设置优化 (Low Timeouts)
- 在 `verify_url_async`、`fetch_yahoo_async`、`fetch_ddg_async` 以及 parent page/sitemap 抓取中，将 HTTP 请求的超时设置由原来的 10~15 秒统一缩短至 **6 秒**。

### 2. 候选池预算限制 (Budget Limits)
- 对搜索发现的链接列表 `filtered_candidates` 限制最高取前 **15** 个。
- 对路径截断产生的链接列表 `all_truncated` 限制最高取前 **15** 个。
- 对爬取的 Sitemap 匹配链接数量限制最高取前 **15** 个。
- 对 parent_roots 扫描数量限制最高取前 **5** 个，提取到的 parent page 子链接 `all_sub_links` 限制最高取前 **15** 个。
- 对 Sitemap 解析时，过滤掉路径深度（斜杠 '/' 数量）超过 5 级的 URL，防止扫到无意义深层子目录。

### 3. 并发 sitemap 及 parent page 链接检验
- 在 Step 4 中，改变原本的 `for s_url in sitemap_urls: verify_url(s_url)` 的同步循环结构。
- 提取所有的 `sitemap_urls` 并调用 `verify_candidates_async` 并发发起 aiohttp 校验请求，秒级完成整批验证。
- 提取所有的 `all_sub_links` 并调用 `verify_candidates_async` 并发发起 aiohttp 校验请求。

### 4. 全局 45 秒超时硬保护 (Global Timeout & Fast Exit)
- 启动主动探测时记录 `start_time`。
- 定义 `GLOBAL_TIMEOUT_SECS = 45`。
- 新增 `check_timeout()` 辅助函数：
  ```python
  def check_timeout():
      elapsed = (datetime.datetime.now() - start_time).total_seconds()
      if elapsed > GLOBAL_TIMEOUT_SECS:
          log_attempt(f"CRITICAL: Active discovery timed out after {elapsed:.1f} seconds.")
          raise TimeoutError("Global discovery timeout exceeded")
  ```
- 在进入各个主要步骤前调用 `check_timeout()`。
- 在 `asyncio.run(verify_candidates_async(...))` 外部包裹 `asyncio.wait_for(..., timeout=remaining_seconds)`，将实时计算出的剩余超时时间传入，当发生超时事件时抛出 `TimeoutError` 并捕获它，输出错误日志后优雅地退出，防止进程挂起。

## Verification Plan

### 1. 单元测试校验
运行 pytest 确认新用例通过：
```bash
python3 -m pytest tests/test_discover_source.py
```

### 2. 整体流水线与主动探测模拟测试
1. 清空某一现有基金的 `confirmed_url`，使其触发 stale update。
2. 运行 `scripts/run_all.py` 并计算耗时，验证其在未搜到或搜到时均可在合理时间（<45秒）内终止，不发生 8 分钟卡死。
