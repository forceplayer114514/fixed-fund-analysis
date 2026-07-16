---
name: extend_extractor
description: "扩展候选提取模式:处置 add/update_fixed_fund 运行期诊断包(no_candidates/大量 pending),先确认数据存在性再广采样格式变体,加 candidates.py 模式 + 测试 + golden 回归验收后重新入库。"
---

# /extend_extractor <fund_id或诊断包路径>

## 职责边界
承接 `/add_fixed_fund`、`/update_fixed_fund` 运行期代码冻结后的诊断包，唯一允许改
`lib/candidates.py`（加候选模式）+ `tests/test_candidates.py`（补测试）的入口。
**由主会话/强模型执行**，禁止派 `general-purpose`/flash 子代理写这部分代码——本
skill 处理的是格式漂移导致的正则/候选逻辑改动，需要人读 PDF 原文判断口径，
不是可批量委派的确定性任务。

## 输入 / 环境
输入：`fund_id` + 运行期输出的诊断包（问题月列表、`review_reason` 统计、
`pending_review` id、文本缓存路径）。同 `/add_fixed_fund`（skills 工作区、DB 路径、
写库方式），铁律见 `skills/CLAUDE.md`。

## 工作流

### 1. 数据存在性确认先行
**先回答"PDF 里到底有没有这个值"，而不是默认"提取器有 bug"去找。** 对每个问题月，
grep 该基金的文本缓存（`data/pdf_cache/<fund_id>/YYYY-MM.txt`，无缓存则先跑一次
`parse_pdf_text` 落盘，见 `lib/extract.py`）确认原文是否存在可辨认的收益数值：
- 原文确无对应数值 → 该月是缺口，走 `confirmed_gaps`，不是提取器问题，到此结束
- 原文有数值但候选池未捕获 → 进入步骤 2

### 2. 全区间广采样格式变体
**grep 该基金全部月份的文本缓存（非抽 2-3 个样本）**，列出格式变体分布及各自
起止区间（如"2019-01~2023-01 用 A 格式，2023-02~2023-08 加 NTA 前缀，
2023-09 起脚注标记，2024 起改用括号负数"）。一次看全漂移谱系，避免只见局部
样本、改完又发现漏 N 个月被迫二轮返工。

### 3. 加候选模式
在 `lib/candidates.py` 加 `pattern_<issuer>(text) -> list[ReturnCandidate]`，
覆盖步骤 2 列出的全部格式变体，`priority=0`（专属模式），进
`CANDIDATE_PATTERNS`。规则同 `docs/pipeline.md` §三。

### 4. 补测试
`tests/test_candidates.py` 加测试用例，每个格式变体至少一条（新旧格式、
脚注、括号负数等步骤 2 发现的全部分支都要覆盖，不只测最新格式）。

### 5. 验收（先测试，再全库回归，最后才重跑入库）
```bash
cd skills && python3 -c "import pytest; pytest.main(['tests/', '-q'])"
cd skills && python3 -m lib.ingest regress --fund-id <fund_id>   # 单基金先看
cd skills && python3 -m lib.ingest regress                        # 全库零回归
```
`regress` 只读、不需要 write token；`value_drift`/`coverage_regression` 非空
说明新模式动了其他基金/月份的既有正确值，必须查清原因（多半是新正则过于宽泛
命中了别处），改到全库零回归为止。**跳过这一步、直接重跑入库属于流程违规。**

### 6. 正式重跑入库
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest update --fund-id <fund_id>
```
确认诊断包里的问题月已 `resolved` 或转入合理 `pending_review`（不再是
`no_candidates`）。

## 完成标准
- [ ] 每个问题月已判定为"真缺口"或"已加模式覆盖"，无遗留 `no_candidates`
- [ ] 广采样发现的全部格式变体都有对应测试用例
- [ ] `pytest tests/` 全绿
- [ ] `python3 -m lib.ingest regress`（全库）零 `value_drift`/`coverage_regression`
- [ ] 目标基金重新入库，诊断包里的问题月已解决
