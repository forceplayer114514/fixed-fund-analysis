---
name: fund_analysis_history
description: "List and delete historical funds."
disable-model-invocation: true
---

# /fund_analysis_history

## Workflow
1. Retrieve historical fund IDs:
   ```bash
   python3 -c 'import yaml; print("\n".join(yaml.safe_load(open("references/fund_registry.yaml")) or []))'
   ```
2. Prompt user using `AskUserQuestion` with `multiSelect: true` to select funds to delete.
3. Clean up selected funds:
   ```bash
   python3 scripts/cleanup_funds.py --funds <selected_fund_1> <selected_fund_2> ...
   ```

## Completion Criterion
- Selected funds removed from `references/fund_registry.yaml`.
- Data folders for deleted funds removed.
