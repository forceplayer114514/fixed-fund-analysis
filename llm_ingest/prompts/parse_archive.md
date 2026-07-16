# 归档页解析 prompt

给你一张已经抓取好的 HTML/markdown 页面 (发行商归档页)。

任务: 从中枚举**每个月的 PDF 月报链接**。

关注:
- 明显的月份文本 (如 "Jan 2025 Monthly Report") + href 到 PDF (.pdf 结尾)。
- 若链接文本没月份, 但 URL 含月份 (如 monthly-report-202501.pdf), 从 URL 提。
- 忽略 gross/factsheet/PDS 等非月报, 忽略非 PDF 链接。
- 若归档明显还有更多页 (分页链 / "Load More" / 只显示近期), 报 has_more_pages=true 并说明如何取下一页。

排除:
- 单份最新报告链接 (未列多月的页面, 通常 slug 是固定 latest-monthly-report.pdf) 不算归档。

输出 (只输出 JSON):
{
  "links": [
    {"ym": "YYYY-MM", "url": "<https://...>"},
    ...
  ],
  "has_more_pages": <bool>,
  "next_page_hint": "<如何取下一页, 或 null>",
  "unparseable_count": <int, 页面上是 PDF 但月份识不出的数>
}
