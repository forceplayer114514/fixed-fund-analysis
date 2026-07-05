import os
import sys
import argparse
import json
import datetime

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def validate_time_series(time_series, fund_id):
    if not time_series:
        raise ValueError(f"Time series data is empty for {fund_id}.")
        
    print(f"Validating time series of length {len(time_series)}...")
    
    # Sort chronologically by date to be sure
    time_series.sort(key=lambda x: x["date"])
    
    # Check that each item has required keys
    for idx, dp in enumerate(time_series):
        if "date" not in dp or "net_return" not in dp or "nav" not in dp:
            raise KeyError(f"Missing required keys (date, net_return, nav) at index {idx}.")
        try:
            # Validate types
            datetime.datetime.strptime(dp["date"], "%Y-%m-%d")
            float(dp["net_return"])
            float(dp["nav"])
        except Exception as e:
            raise TypeError(f"Invalid data type at index {idx}: {e}")
            
    # Check for gaps between consecutive months
    for i in range(1, len(time_series)):
        prev_date = datetime.datetime.strptime(time_series[i-1]["date"], "%Y-%m-%d").date()
        curr_date = datetime.datetime.strptime(time_series[i]["date"], "%Y-%m-%d").date()
        
        # Check if consecutive months
        expected_month = prev_date.month + 1
        expected_year = prev_date.year
        if expected_month > 12:
            expected_month = 1
            expected_year += 1
            
        if curr_date.year != expected_year or curr_date.month != expected_month:
            raise ValueError(f"GAP DETECTED: Gap found in time-series between {time_series[i-1]['date']} and {time_series[i]['date']}.")
            
    print("Time series validation passed. No gaps found.")

def main():
    parser = argparse.ArgumentParser(description="Validate fund return time series data for gaps and types.")
    parser.add_argument("--fund", required=True, help="Fund ID (e.g. stake_accumulate)")
    parser.add_argument("--date", required=True, help="Data period (e.g. 2026-05)")
    args = parser.parse_args()
    
    fund_id = args.fund
    date_period = args.date
    
    print(f"=== Starting Validation for {fund_id} ({date_period}) ===")
    
    raw_file = os.path.join(BASE_DIR, "data", "raw", fund_id, f"{date_period}.json")
    if not os.path.exists(raw_file):
        print(f"CRITICAL: Raw structured JSON not found at {raw_file}. Please run parse_factsheet.py first.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Validate schema keys
        required_keys = ["fund_id", "fund_name", "apir_code", "data_period", "time_series"]
        for k in required_keys:
            if k not in data:
                raise KeyError(f"Missing required top-level key: {k}")
                
        # Validate time series gaps
        validate_time_series(data["time_series"], fund_id)
        
        # Save to cleaned directory
        cleaned_dir = os.path.join(BASE_DIR, "data", "cleaned", fund_id)
        os.makedirs(cleaned_dir, exist_ok=True)
        cleaned_file = os.path.join(cleaned_dir, f"{date_period}.validated.json")
        
        with open(cleaned_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        print(f"SUCCESS: Validation completed successfully. Cleaned data saved to {cleaned_file}")
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL: Validation FAILED for {fund_id}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
