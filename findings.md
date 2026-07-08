# Findings & Decisions

## Requirements
- 必须移除 Stake 的捏造数据。
- report.md 必须包含异常复核清单。
- Bentham 基金优先使用正文 commentary 作为数值真值。
- anomaly_detection 必须更稳健 (Median+MAD)，并携带 commentary 供复核。
- 修复 `clean_spacing` 将脚注 1 和数值焊死的 bug。

## Research Findings
- `generate_report.py` 未读取 `.validated.json` 中的 anomalies 字段。
- Stake 基金有强制硬编码的 backfill 逻辑（2024-12 到 2025-02 填 0.00657）。
- Bentham 的 commentary 在全部 114 个 PDF 中存在，覆盖率 100%。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Commentary 优先 | 100% 覆盖率，且无列歧义和脚注歧义。 |
| 使用 MAD (Median Absolute Deviation) 替换 StdDev | 异常值（如把季度数据填入）会显著拉大 StdDev，掩盖异常本身，使用稳健估计更好。 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
|       |            |

## Resources
- `scripts/parse_factsheet.py`
- `scripts/generate_report.py`
- `scripts/anomaly_detection.py`
- `scripts/validate_data.py`

## Visual/Browser Findings
- 无
