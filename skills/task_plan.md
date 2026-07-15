# Perpetual Credit Income Trust 基金添加计划

## 基金标识
- **基金名称**: Perpetual Credit Income Trust
- **基金ID**: `perpetual_credit_income_trust`
- **APIR 代码**: 待确认 (可能为 PCI.ASX 或特定 APIR，Perpetual Credit Income Trust 是 ASX 上市的 LIC/LIT，代码为 `PCI`)

## 阶段规划

### 阶段 1: 探测数据源（4分钟墙钟预算，单月合成优先）
1. 用 `mcp__search__search` 寻找 Perpetual Credit Income Trust (PCI) 官网
2. 查找月度报告/factsheet/performance 归档页
3. 下载最新月报 PDF 直链，检查内容：
   - 提取当月收益 (Commentary / performance table)
   - 确认是 gross 还是 net
   - 寻找是否存在 Year×Month 完整历史收益表
4. 若官网有归档，合成全量月 PDF 链接序列；若无，找 Wayback CDX 备份或 fundmonitors 免费源

### 阶段 2: 运行候选策略遍历
1. 运行 `python3 -m lib.strategies probe` 获取 `DiscoveryReport`
2. 根据 `DiscoveryReport` 的 `best_strategy` 决策入库路径

### 阶段 3: 执行入库与校验
1. 使用 `python3 -m lib.ingest` 执行入库 (带 `FUND_DB_WRITE_TOKEN`)
2. 确认入库条数，校验 NAV 复利，检查无 data gap

### 阶段 4: 输出提示
1. 输出入库结果 (时间跨度，月数)
2. 提示用户在 webapp 触发 metrics 计算
