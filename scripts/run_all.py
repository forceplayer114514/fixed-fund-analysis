import os
import sys
import subprocess
import yaml
import datetime

import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

def run_cmd(args):
    print(f"\nRunning command: {' '.join(args)}")
    res = subprocess.run(args, capture_output=True, text=True, cwd=BASE_DIR)
    if res.returncode != 0:
        print(f"Error executing command: {' '.join(args)}", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    print(res.stdout)

def main():
    print("==================================================================")
    print("Australian Fixed Income Fund Comparison Pipeline (End-to-End Run)")
    print("==================================================================")
    
    parser = argparse.ArgumentParser(description="Run comparison pipeline for fixed income funds.")
    parser.add_argument("--funds", nargs="+", help="Optional: run pipeline for specific fund IDs.")
    args = parser.parse_args()

    # 1. Load registry
    if not os.path.exists(REGISTRY_PATH):
        print(f"CRITICAL: Registry not found at {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}

    if args.funds:
        funds = []
        for f_id in args.funds:
            if f_id not in registry:
                print(f"CRITICAL: Fund '{f_id}' is not registered in fund_registry.yaml. Please add it first.", file=sys.stderr)
                sys.exit(1)
            funds.append(f_id)
    else:
        funds = list(registry.keys())
        
    print(f"Target funds: {', '.join(funds)}")
    
    # 1.5. Freshness Check
    print("\n--- Pipeline Pre-check: Evaluating Data Freshness ---")
    stale_funds = []
    parsed_funds = {}  # Store the latest date for each fund (both fresh and stale)
    current_date = datetime.datetime.now()
    
    for fund in funds:
        fund_dir = os.path.join(BASE_DIR, "data", "raw", fund)
        latest_date = None
        
        if os.path.exists(fund_dir):
            json_files = [f for f in os.listdir(fund_dir) if f.endswith(".json") and f != "manifest.json"]
            if json_files:
                json_files.sort()
                latest_date = json_files[-1].replace(".json", "")
                
        if latest_date:
            try:
                # Expecting format YYYY-MM
                data_year, data_month = map(int, latest_date.split("-"))
                month_diff = (current_date.year - data_year) * 12 + (current_date.month - data_month)
                
                if month_diff <= 2:
                    print(f"[Skip] Fund '{fund}' has recent data up to {latest_date} (diff: {month_diff} months). Skipping Web Fetch and PDF parsing.")
                    parsed_funds[fund] = latest_date
                else:
                    print(f"[Stale] Fund '{fund}' data is from {latest_date}, older than threshold (diff: {month_diff} months). Triggering fresh web fetch...")
                    stale_funds.append(fund)
            except Exception as e:
                print(f"[Warning] Failed to parse date '{latest_date}' for fund '{fund}': {e}. Treating as stale.")
                stale_funds.append(fund)
        else:
            print(f"[Missing] Fund '{fund}' has no local data. Triggering web fetch...")
            stale_funds.append(fund)

    
    # 2. Run Pipeline Steps (Only for stale_funds)
    if stale_funds:
        print(f"\nExecuting full fetch & parse pipeline for: {', '.join(stale_funds)}")
        
        # Step 0: URL Discovery (Active verification)
        print("\n--- Pipeline Step 0: Running URL Discovery ---")
        for fund in stale_funds:
            run_cmd(["python3", "scripts/discover_source.py", "--fund", fund])
            
        # Step 1: Fetch raw assets
        print("\n--- Pipeline Step 1: Fetching Web Assets ---")
        for fund in stale_funds:
            run_cmd(["python3", "scripts/fetch_web.py", "--fund", fund])
            
        # Step 2: Parse raw assets into structured JSON
        print("\n--- Pipeline Step 2: Parsing Factsheets ---")
        for fund in stale_funds:
            run_cmd(["python3", "scripts/parse_factsheet.py", "--fund", fund])
            
            # Check output files to see the generated latest date
            fund_dir = os.path.join(BASE_DIR, "data", "raw", fund)
            json_files = [f for f in os.listdir(fund_dir) if f.endswith(".json") and f != "manifest.json"]
            if json_files:
                json_files.sort()
                latest_date = json_files[-1].replace(".json", "")
                parsed_funds[fund] = latest_date
    else:
        print("\nAll target funds are fresh. Skipping Steps 0, 1, and 2.")
            
    # Step 3 & 4: Validate and compute metrics (For all funds that have a parsed date)
    print("\n--- Pipeline Step 3 & 4: Data Validation and Metrics Calculation ---")
    for fund, date in parsed_funds.items():
        run_cmd(["python3", "scripts/validate_data.py", "--fund", fund, "--date", date])
        run_cmd(["python3", "scripts/metrics.py", "--fund", fund, "--date", date])
        
    # Step 5: Report compilation
    print("\n--- Pipeline Step 5: Compiling Comparison Report ---")
    if parsed_funds:
        cmd = ["python3", "scripts/generate_report.py"]
        if args.funds:
            cmd.extend(["--funds"] + args.funds)
        run_cmd(cmd)
    else:
        print("No valid parsed data found for any fund. Skipping report generation.")
    
    print("\n==================================================================")
    print("Pipeline run completed successfully!")
    print("==================================================================")

if __name__ == "__main__":
    main()
