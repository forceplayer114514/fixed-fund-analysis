# Task Plan: 修复固定收益基金数据解析与异常汇报流程

## Goal
修复 Bentham 基金解析中的“季度误当月度”bug（由 clean_spacing 脚注焊死及列猜测引起），移除 Stake 基金的违规捏造数据，并将异常检测结果可靠地输出到最终 Markdown 报告中，同时增强异常检测的稳健性。

## Current Phase
Phase 1

## Phases

### Phase 1: 清理严重违规数据 (Stake)
- [ ] 移除 `parse_factsheet.py` 中 Stake Accumulate 的 3 个月捏造数据（Dec 2024-Feb 2025）。
- [ ] 确保缺失的月份按规定抛出缺口异常或让上游处理，不进行捏造。
- **Status:** pending

### Phase 2: 修复报告生成 (生成包含异常复核的报告)
- [ ] 修改 `generate_report.py`，从 `.validated.json` 中读取 anomalies。
- [ ] 在报告中增加专门的“数据异常与人工复核清单”章节。
- **Status:** pending

### Phase 3: 优化数据抽取源与异常检测增强
- [ ] 在 `parse_factsheet.py` 的 `_process_single_bentham_pdf` 中，提取 commentary 作为主源，原正则提取作为对比。
- [ ] 只有在 commentary 缺失时，才回退到修复后的表格解析。
- [ ] 修改 `anomaly_detection.py`，使用 median 和 MAD (稳健统计)。
- [ ] 将 commentary 真值一同带入异常记录中，方便人工比对。
- **Status:** pending

### Phase 4: 修复基础文本清洗与表格抓取
- [ ] 修改 `clean_spacing`，避免脚注 1 焊死到数值上。
- [ ] 移除基于首字符 "1" 猜测的脆性规则 1/2/3。
- [ ] 在提取时加入列头安全校验（可选/如果有时间）。
- [ ] 修改 `As at` 和 Stake commentary 抓取的隐患。
- **Status:** pending

### Phase 5: 测试与端到端验证
- [ ] 运行单元测试并补充失败/边缘测试（特别是脚注边界）。
- [ ] 运行 `run_all.py` 执行端到端流程。
- [ ] 验证 `data/output/report.md` 是否正确包含异常信息。
- **Status:** pending

## Key Questions
1. 移除 Stake 捏造数据后，该基金序列会少 3 个月，下游 `metrics.py` 和 `generate_report.py` 是否能优雅处理较短的序列？
2. 异常报告章节的表格应该包含哪些列，才能让使用者一目了然？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
|          |           |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- 优先处理数据捏造，这是底线问题。
- 测试优先，不要乱跑 pipeline。

### Phase 6: 查明历史造假代码来源并建立代码级防范机制
- [ ] 使用 git 追溯 `parse_factsheet.py` 中 Stake 基金数据回填逻辑的提交历史。
- [ ] 分析造假代码是如何被引入的（大概率是为了满足某种对齐或完整性约束而产生的幻觉/过度修复）。
- [ ] 提出并实施代码层面的防范机制，确保大模型无法在解析器中硬编码伪造数据。
- **Status:** in_progress
