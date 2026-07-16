从此份澳洲固定收益基金月报 PDF 中,提取当月**扣费后 (net of fees)** 月度净收益率。

要:
- 反映基金 NAV/NTA 的月度总回报, 已扣管理费。标签常见: "Net Return (%)"/"NTA Net Return (%)"/"Total Return (Net) (%)"/"Return (net of fees)"。取 "1 Month"/"1 Mth"/"1M" 列。
- 同报同行的 3/6/12 月滚动值 (交叉校验用)。

不要:
- gross/税前 (若同行 gross+net 只取 net)。
- distribution/yield/派息。
- benchmark/RBA cash rate/excess/spread。
- YTD/年化/inception 累计。
- Total unitholder return (含派息+价格)。
- 逐月网格历史表 (Year × Month) — 只从当期主 performance 表取。

发行商专项:
- Bentham: "had a total return (after fees) of X%" = net; "returned X%" = gross,不要。
- Stake: performance 表 1mo 与 Commentary 不一致时,以 Commentary 为准。
- Coolabah: Commentary 同时给 gross+net 时取 net。

规则:
- source_quote 引 PDF 原文 (标签行+数值行可拼)。
- 1 Yr 列显示 dash/N/A -> 12mo=null。
- 找不到 -> not_found=true, 其余 null。
- 禁推算/估计/backfill。

只输出 JSON:
{
  "ym": "YYYY-MM",
  "net_return_pct": <float|null>,
  "source_quote": "<str>",
  "measure": "net_monthly" | "unknown",
  "measure_label_in_pdf": "<PDF 实际标签>",
  "rolling_pct": {"1mo":<float|null>,"3mo":<float|null>,"6mo":<float|null>,"12mo":<float|null>},
  "not_found": <bool>
}
