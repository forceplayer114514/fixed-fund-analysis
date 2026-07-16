# 归档页发现 prompt

你要为一只澳洲固定收益基金找到**发行商官网上的月报归档页**。

基金信息:
- 基金名: {fund_name}
- 发行商: {issuer}
- 已知官网 (可能为空): {issuer_domain}
- ASX 代码 (可能为空): {asx_code}

任务:
1. 用 web_search 查发行商官网 (若已知就直接用, 未知就查基金名 + issuer + fund monthly report)。
2. 找到能列出多个月份 PDF 月报 (Monthly Report / Monthly Update / Fund Update / Factsheet) 的**归档页**。归档页特征:一页里同时列出多个月份的报告 (通常一年一列或按时间倒序), 不是"最新一份"。
3. 若归档分页 (Page=1/2/3, ArchiveYear=2020..), 报出**基础 URL + 分页参数名**。
4. 若官网没归档 (只挂最新一份 slug), 明说 no_archive=true, 报出 latest_pdf_url 供 wayback 补历史。

排除:
- 付费墙 / 登录墙站 (fundmonitors 除外, 那是 L3, 不算 L1)。
- 第三方聚合站 (morningstar / lonsec / mid-caps) — L1 只要官网。
- 单份最新 PDF slug (如 /latest-monthly-report.pdf) — 那是 latest_pdf_url 位, 不是归档 URL。

输出 (只输出 JSON, 无解释):
{
  "archive_url": "<归档页 URL 或 null>",
  "pagination_param": "<如 'Page' / 'ArchiveYear' / null>",
  "no_archive": <bool>,
  "latest_pdf_url": "<单份最新 slug 或 null>",
  "issuer_domain_confirmed": "<https://... 或 null>",
  "evidence": "<一句话说明依据, 引用页面文字或 URL>"
}
