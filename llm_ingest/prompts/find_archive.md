找该基金**发行商官网**的月报归档页(列多个月份 PDF 的页面)。

输入:
- 基金名: {fund_name}
- 发行商: {issuer}
- 已知官网: {issuer_domain}
- ASX: {asx_code}

**先联网搜索,再回答**(不搜必幻觉过期域名)。搜到多个候选先列出,再判哪个是**归档页**(一页列多月),哪个是**单份最新**(no_archive=true, 走 wayback)。

排除: 付费墙/登录墙、第三方聚合站(morningstar/lonsec)、单份 PDF slug。

只输出 JSON:
{
  "archive_url": "<归档页 URL 或 null>",
  "pagination_param": "<'Page'/'ArchiveYear'/null>",
  "no_archive": <bool>,
  "latest_pdf_url": "<最新单份 slug 或 null>",
  "issuer_domain_confirmed": "<https://... 或 null>",
  "evidence": "<一句话依据>"
}
