# /update_fixed_fund <基金名称或标识>

调用 update_fixed_fund skill 更新已注册澳洲固定收益基金的最新月度收益：读 funds 表配置、MCP 抓取最新月、upsert 新增月份到 monthly_returns。仅入库原始数据，不算指标。

$ARGUMENTS
