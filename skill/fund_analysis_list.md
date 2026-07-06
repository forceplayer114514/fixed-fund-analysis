---
name: fund_analysis_list
description: "Multi-fund comparison mode using interactive multi-select."
disable-model-invocation: true
---

# /fund_analysis_list [optional_new_fund]

## Workflow
1. Retrieve historical fund IDs:
   ```bash
   python3 -c 'import yaml; print("\n".join(yaml.safe_load(open("references/fund_registry.yaml")) or []))'
   ```
2. If `[optional_new_fund]` is provided and not registered, resolve APIR and register it first.
3. Prompt user using `AskUserQuestion` with `multiSelect: true` to select target funds.
4. Run pipeline for selected funds:
   ```bash
   python3 scripts/run_all.py --funds <selected_fund_1> <selected_fund_2> ...
   ```

## Completion Criterion
- User selection recorded.
- Pipeline execution output shown and comparison report generated.
