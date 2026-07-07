import os
import sys
import subprocess
import yaml
import datetime
import argparse
import threading
import re
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH: str = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

print_lock: threading.Lock = threading.Lock()

def run_cmd(args: List[str]) -> None:
    print(f"\nRunning command: {' '.join(args)}")
    res = subprocess.run(args, capture_output=True, text=True, cwd=BASE_DIR)
    if res.returncode != 0:
        print(f"Error executing command: {' '.join(args)}", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    if res.stdout:
        print(res.stdout)

def run_cmd_for_fund(args: List[str], log_lines: List[str]) -> None:
    log_lines.append(f"Running command: {' '.join(args)}")
    res = subprocess.run(args, capture_output=True, text=True, cwd=BASE_DIR)
    if res.returncode != 0:
        log_lines.append(f"Error executing command: {' '.join(args)}")
        if res.stdout:
            log_lines.append(res.stdout)
        if res.stderr:
            log_lines.append(res.stderr)
        raise RuntimeError(f"Command failed: {' '.join(args)}\nError: {res.stderr}")
    if res.stdout:
        log_lines.append(res.stdout)

def run_single_fund_pipeline(
    fund_id: str,
    latest_date: Optional[str],
    is_stale: bool
) -> Tuple[str, Optional[str]]:
    log_lines: List[str] = []
    log_lines.append(f"\n==================================================================")
    log_lines.append(f"Starting pipeline for fund: {fund_id} (stale={is_stale}, base_date={latest_date})")
    log_lines.append(f"==================================================================")

    try:
        # Step 0: URL Discovery (Active verification)
        if is_stale:
            log_lines.append(f"\n--- [{fund_id}] Pipeline Step 0: Running URL Discovery ---")
            run_cmd_for_fund(["python3", "scripts/discover_source.py", "--fund", fund_id], log_lines)

            # Step 1: Fetch raw assets
            log_lines.append(f"\n--- [{fund_id}] Pipeline Step 1: Fetching Web Assets ---")
            run_cmd_for_fund(["python3", "scripts/fetch_web.py", "--fund", fund_id], log_lines)

            # Step 2: Parse raw assets into structured JSON
            log_lines.append(f"\n--- [{fund_id}] Pipeline Step 2: Parsing Factsheets ---")
            run_cmd_for_fund(["python3", "scripts/parse_factsheet.py", "--fund", fund_id], log_lines)

            # Check output files to see the generated latest date
            fund_dir = os.path.join(BASE_DIR, "data", "raw", fund_id)
            if os.path.exists(fund_dir):
                json_files = [f for f in os.listdir(fund_dir) if re.match(r"^\d{4}-\d{2}\.json$", f)]
                if json_files:
                    json_files.sort()
                    latest_date = json_files[-1].replace(".json", "")
                else:
                    latest_date = None
            else:
                latest_date = None

        # Step 3 & 4: Validate and compute metrics
        if latest_date:
            log_lines.append(f"\n--- [{fund_id}] Pipeline Step 3 & 4: Data Validation and Metrics Calculation ---")
            run_cmd_for_fund(["python3", "scripts/validate_data.py", "--fund", fund_id, "--date", latest_date], log_lines)
            run_cmd_for_fund(["python3", "scripts/metrics.py", "--fund", fund_id, "--date", latest_date], log_lines)
        else:
            log_lines.append(f"\n--- [{fund_id}] Warning: No valid parsed data found. Skipping Step 3 & 4. ---")

        log_lines.append(f"\n==================================================================")
        log_lines.append(f"Successfully completed pipeline for fund: {fund_id}")
        log_lines.append(f"==================================================================")

        # Print the entire log buffer for this fund atomically
        with print_lock:
            print("\n".join(log_lines))
        return fund_id, latest_date
    except Exception as e:
        log_lines.append(f"\n==================================================================")
        log_lines.append(f"FAILED pipeline for fund: {fund_id}")
        log_lines.append(f"Error: {e}")
        log_lines.append(f"==================================================================")
        with print_lock:
            print("\n".join(log_lines), file=sys.stderr)
        raise

def main() -> None:
    print("==================================================================")
    print("Australian Fixed Income Fund Comparison Pipeline (End-to-End Run)")
    print("==================================================================")

    parser = argparse.ArgumentParser(description="Run comparison pipeline for fixed income funds.")
    parser.add_argument("--funds", nargs="+", help="Optional: run pipeline for specific fund IDs.")
    args = parser.parse_args()

    # Step 0: Validate Registry Schema (Pre-flight check)
    print("\n--- Pipeline Pre-check 0: Validating fund_registry.yaml ---")
    run_cmd(["python3", "scripts/validate_registry.py"])

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
    stale_funds: List[str] = []
    parsed_funds: Dict[str, str] = {}  # Store the latest date for each fund (both fresh and stale)
    current_date = datetime.datetime.now()

    for fund in funds:
        fund_dir = os.path.join(BASE_DIR, "data", "raw", fund)
        latest_date = None

        if os.path.exists(fund_dir):
            json_files = [f for f in os.listdir(fund_dir) if re.match(r"^\d{4}-\d{2}\.json$", f)]
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

    # 2. Run Pipeline Steps Concurrently
    print("\n--- Running Pipelines Concurrently for All Target Funds ---")
    futures = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for fund in funds:
            is_stale = fund in stale_funds
            fund_latest_date = parsed_funds.get(fund)
            future = executor.submit(run_single_fund_pipeline, fund, fund_latest_date, is_stale)
            futures[future] = fund

    # Collect results and check for failures
    failed_funds: List[str] = []
    for future in as_completed(futures):
        fund_id = futures[future]
        try:
            res_fund_id, latest_date = future.result()
            if latest_date:
                parsed_funds[res_fund_id] = latest_date
        except Exception as e:
            print(f"CRITICAL: Fund '{fund_id}' pipeline execution failed: {e}", file=sys.stderr)
            failed_funds.append(fund_id)

    if failed_funds:
        print(f"\nCRITICAL: The following funds failed to process: {', '.join(failed_funds)}", file=sys.stderr)
        sys.exit(1)

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
