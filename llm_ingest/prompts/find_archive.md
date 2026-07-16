从给定 URL 列表中挑该基金**发行商官网**的月报归档页(列多个月份 PDF 的页面)。

输入:
- 基金名: {fund_name}
- 发行商: {issuer}
- 已知官网: {issuer_domain}
- ASX: {asx_code}

规则:
- 优先选 **issuer 官网域下** 的 URL (如 benthamam.com.au, gcapinvest.com)
- **归档页** = 一页列多月 PDF 链接 (如 /performance, /fund-reports, /monthly-reports)
- **单份最新** = URL 里含具体年月/PDF slug (如 `-may-2025.pdf`) -> no_archive=true, 走 wayback
- 排除: 付费墙/登录墙、第三方聚合站 (fidante/sovereignfinancial/adviservoice/livewire 等只做营销)

只输出 JSON, 无其他文字:
{
  "archive_url": "<归档页 URL 或 null>",
  "pagination_param": "<'Page'/'ArchiveYear'/null>",
  "no_archive": <bool>,
  "latest_pdf_url": "<最新单份 PDF URL 或 null>",
  "issuer_domain_confirmed": "<https://issuer-domain 或 null>",
  "evidence": "<一句话依据>"
}
