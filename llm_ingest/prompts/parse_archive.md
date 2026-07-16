从归档页(HTML/markdown)枚举**每个月的 PDF 月报链接**。

规则:
- 明显月份文本 + href .pdf,或 URL 含月份(monthly-report-202501.pdf)。
- 忽略 factsheet/PDS/gross,只要月报。
- 分页/"Load More"/只显示近期 -> has_more_pages=true 并说下页怎么取。
- 单份最新(未列多月)不算归档。

只输出 JSON:
{
  "links": [{"ym": "YYYY-MM", "url": "<https://...>"}, ...],
  "has_more_pages": <bool>,
  "next_page_hint": "<下页怎么取 或 null>",
  "unparseable_count": <int, 页上是 PDF 但月份识不出的数>
}
