---
name: fund_analysis
description: "Australian Fixed Income Fund Comparison Skill. Discovers factsheet URLs, fetches/downloads performance reports, parses data, runs Geltner unsmoothing & risk metrics, and generates comparison reports for any Australian fixed income fund."
---

# Australian Fixed Income Fund Comparison Skill (fund_analysis)

This skill provides an automated pipeline to discover, fetch, parse, validate, and compute risk-adjusted comparison metrics (such as Geltner unsmoothing, annualized compounded returns, volatilities, and Sortino ratios) for any Australian Fixed Income Fund.

---

## 1. Dynamic Slash Command Interaction Workflow

The skill provides three interactive commands:

### Command 1: `/fund_analysis <fund_name>` (Single Fund Analysis)
- **Use case**: When the user wants to analyze just one new or existing fund.
- **Workflow**:
  1. Load `references/fund_registry.yaml`. If `<fund_name>` is new, use `search_web` to find its APIR code and register it.
  2. Run the pipeline for this specific fund:
     ```bash
     python3 scripts/run_all.py --funds <fund_id>
     ```

### Command 2: `/fund_analysis_list [optional_new_fund]` (Multi-Fund Comparison)
- **Use case**: When the user wants to compare multiple funds, selecting from historical records (and optionally registering a new one).
- **Workflow**:
  1. Execute a python snippet via Bash to get all historical fund IDs:
     ```bash
     python3 -c 'import yaml; print("\n".join(yaml.safe_load(open("references/fund_registry.yaml")) or []))'
     ```
  2. If `[optional_new_fund]` is provided and not in the registry, find its APIR and register it.
  3. Use the **AskUserQuestion** tool (`multiSelect: true`) to ask the user "Which funds would you like to compare?". Provide the historical funds (and the new fund) as options.
  4. Once the user selects the funds (e.g., `fund1`, `fund2`), run the pipeline specifically for them:
     ```bash
     python3 scripts/run_all.py --funds <selected_fund_1> <selected_fund_2>
     ```

### Command 3: `/fund_analysis_history` (History Cleanup)
- **Use case**: When the user wants to view all historical funds and delete selected ones from local storage.
- **Workflow**:
  1. Read the historical funds via Bash:
     ```bash
     python3 -c 'import yaml; print("\n".join(yaml.safe_load(open("references/fund_registry.yaml")) or []))'
     ```
  2. Use the **AskUserQuestion** tool (`multiSelect: true`) to ask "Which historical funds would you like to delete and clean up?".
  3. Once the user makes their selection, run the cleanup script:
     ```bash
     python3 scripts/cleanup_funds.py --funds <selected_fund_1> <selected_fund_2>
     ```
  4. Confirm to the user that the selected funds have been removed from the registry and their data folders deleted.

---

## 2. Pipeline Internal Steps (Executed by `run_all.py`)

### Run Entire Pipeline (All Registered Funds)
```bash
python3 scripts/run_all.py
```

### Run for Specific Funds
```bash
python3 scripts/run_all.py --funds <fund_id1> <fund_id2>
```
