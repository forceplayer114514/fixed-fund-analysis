# 🇦🇺 澳洲固定收益基金量化分析流水线
(Australian Fixed Income Fund Analysis Pipeline)

这是一个端到端的自动化数据流水线，专为澳洲固定收益（Fixed Income）与绝对收益基金设计。它可以自动发现、抓取、解析基金月报（Factsheets），并进行严谨的量化指标计算，最终生成可供投资决策参考的 Markdown 对比报告和 Excel 数据表。

本项目天然兼容 AI Agent（如 Claude Code），内置了完整的 Agent 交互技能定义（Skill），支持通过自然语言对话驱动单基金分析、多基金对比及历史数据清理。

## ✨ 核心特性

- **🤖 AI Agent 原生集成**：内置 `skill/SKILL.md`，支持大语言模型通过命令行交互和自动调度。
- **🕷️ 智能增量爬取**：自带数据新鲜度检测（默认 2 个月阈值），避免对基金官网的无效请求和被反爬封禁。
- **📊 深度量化风控计算**：
  - **Geltner 去平滑（Unsmoothing）修正**：基于 Ljung-Box 检验一阶自相关性，自动还原被“人为平滑”的真实年化波动率和 Sortino 比率。
  - **动态无风险利率扣除**：自动爬取澳洲央行（RBA）历史 Cash Rate，逐月精准计算超额收益（Alpha）。
  - **最大回撤（Max Drawdown）**追踪。
  - 信用利差与杠杆收益静态分解。

---

## 🚀 快速开始

### 1. 环境依赖
项目基于 Python 3.12+，需要安装以下依赖：
```bash
pip install requests beautifulsoup4 pypdf pandas openpyxl pyyaml
```

### 2. 基金注册与配置
在 `references/fund_registry.yaml` 中配置目标基金。示例：
```yaml
coolabah_long_short_credit:
  apir_code: SLT2562AU
  fund_name: Coolabah Long-Short Credit Fund (Direct Class)
  confirmed_url: 'https://coolabahcapital.com/...'
  fetch_method: requests+BeautifulSoup
```

### 3. 运行流水线
运行所有注册的基金，或指定特定的基金进行分析：
```bash
# 运行所有基金的完整流水线
python3 scripts/run_all.py

# 仅分析特定的基金
python3 scripts/run_all.py --funds coolabah_long_short_credit stake_accumulate

# 清理指定基金的历史数据
python3 scripts/cleanup_funds.py --funds old_fund_id
```

执行完毕后，报告将输出在 `data/output/` 目录下：
- `report.md`: 综合对比报告（包含 AI 投资建议占位符）。
- `fund_data.xlsx`: 每月净值及超额收益率历史明细表。

---

## 🛠️ 开发者指南：如何修改与扩展代码

项目的核心逻辑解耦为多个独立的 Python 脚本，以流水线（Pipeline）的形式由 `run_all.py` 调度。如果你想为项目增加新功能或适配新基金，请参考以下指南：

### 📁 目录架构
```text
.
├── scripts/                # 核心流水线脚本
├── references/             # 配置与注册表
├── data/
│   ├── raw/                # 下载的 PDF/HTML 及初始提取的 JSON
│   ├── cleaned/            # 校验后的数据及计算好的 metrics.json
│   └── output/             # 最终的 Markdown 报告和 Excel 数据表
└── skill/                  # AI Agent 的能力定义文件
```

### 1. 适配新基金公司的 PDF 报告格式
**目标脚本：`scripts/parse_factsheet.py`**
不同的基金公司有不同的月报排版。当前脚本主要使用 `pypdf` 读取文本并通过正则表达式提取历史净值表格。
- **如何修改**：如果新基金的 PDF 无法被正确解析，请在 `parse_factsheet.py` 中新增一个特定的解析函数（例如 `def parse_macquarie(...)`）。
- **数据结构**：无论怎么解析，最终必须输出统一格式的时间序列数组，并存为 JSON，包含：`date` (YYYY-MM-DD), `net_return` (当月净收益), `nav` (累计净值)。
- **回溯填补（Backfill）**：如果该基金早期月份的 PDF 缺失，脚本内提供了根据“成立以来累计收益”自动倒推回溯填充平均月化收益的逻辑，可复用此功能保证数据区间完整。

### 2. 添加新的量化/金融指标
**目标脚本：`scripts/metrics.py`**
如果你想添加新的金融指标（比如 Sharpe Ratio，卡玛比率 Calmar Ratio 等）。
- **如何修改**：
  1. 在 `metrics.py` 中编写具体的数学计算函数。
  2. 在 `main()` 提取出 `returns`, `nav_series`, `excess_returns` 等基础数组后调用你的新函数。
  3. 将计算结果加入到 JSON 字典 `metrics_result["original_metrics"]` 及 `unsmoothed_metrics` 节点中。

### 3. 自定义最终生成的对比报告
**目标脚本：`scripts/generate_report.py`**
该脚本负责聚合 `data/cleaned/` 目录下各个基金的 `.metrics.json` 文件。
- **如何修改**：
  - 如果在步骤 2 中添加了新指标，需要在这里的 `md.append("| 基金名称 | ... | 新指标 |")` 表头中添加新列。
  - 并在下方的循环中通过 `m["original_metrics"].get("new_metric", 0.0)` 提取并格式化拼接入 Markdown 行。
  - Excel 的格式生成和样式调整在底部的 `generate_excel_data` 函数中。

### 4. 调整防冗余/缓存时间阈值
**目标脚本：`scripts/run_all.py`**
- 默认情况下，如果本地最新的 JSON 数据距离当前物理时间 $\le 2$ 个月，脚本会标记其为 `[Skip]` 并跳过网络抓取步骤。
- 如果你需要更高频的更新或强制抓取，可以修改 `run_all.py` 内部 `month_diff <= 2` 的阈值判断。

## 📄 协议与声明
本项目作为数据分析和自动化流展示，抓取的数据仅供研究用途，不构成任何财务或投资建议。
