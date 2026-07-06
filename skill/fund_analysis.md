---
name: fund_analysis
description: "Australian Fixed Income Fund Comparison Skill."
disable-model-invocation: true
---

# /fund_analysis <fund_name>

## Workflow
1. Load `references/fund_registry.yaml`. If `<fund_name>` is not registered, use web search to find its APIR code, register it.
2. Run pipeline for this fund:
   ```bash
   python3 scripts/run_all.py --funds <fund_id>
   ```

## Completion Criterion
- Fund registered in `references/fund_registry.yaml`.
- Pipeline output printed showing successful run for `<fund_id>`.
