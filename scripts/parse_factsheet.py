import os
import sys
import argparse
import datetime
import re
import json
from typing import List, Dict, Optional, Tuple, Any, Set
from bs4 import BeautifulSoup
try:
    import fitz
except ImportError:
    import sys
    print('CRITICAL: PyMuPDF is not installed.', file=sys.stderr)
    sys.exit(1)
import concurrent.futures
import multiprocessing
import yaml

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}

def clean_spacing(text: str) -> str:
    # Condense multiple spaces into single space, but DO NOT weld numbers and footnotes
    cleaned = re.sub(r'\s+', ' ', text)
    # Re-add a safe boundary if a footnote seems to be stuck to a number
    # (Though simple space condensing usually doesn't weld them unless they were originally separated by a newline/space that we strip)
    # The original welding happened because of the lookbehind `(?<=\b\w) (?=\w\b)` stripping spaces between single characters. We simply remove that dangerous regex.
    return cleaned

def get_last_day_of_month(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

def extract_month_prefix(filename: str) -> Optional[str]:
    # 1. Look for YYYYMMD or YYYYMMDD (e.g. 20170131-GIF-Monthly-Report.pdf or 2023031-GIF-Monthly-Report.pdf)
    date_match = re.search(r'(\b|[^0-9])(\d{4})(\d{2})(\d{1,2})(\b|[^0-9])', filename)
    if date_match:
        year = int(date_match.group(2))
        month = int(date_match.group(3))
        return f"{year}-{month:02d}"

    # 2. Look for YYYYMM (e.g. GIF-Monthly-Report-202502.pdf)
    date_match_short = re.search(r'(\b|[^0-9])(\d{4})(\d{2})(\b|[^0-9])', filename)
    if date_match_short:
        year = int(date_match_short.group(2))
        month = int(date_match_short.group(3))
        return f"{year}-{month:02d}"

    # 3. Look for month names and year (e.g. April-2025, Nov-2024)
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
    }
    month_names_pattern = "|".join(month_map.keys())
    m = re.search(r"(" + month_names_pattern + r")[-_]*(\d{4})", filename, re.IGNORECASE)
    if m:
        month = month_map[m.group(1).lower()]
        year = int(m.group(2))
        return f"{year}-{month:02d}"

    m = re.search(r"(\d{4})[-_]*(" + month_names_pattern + r")", filename, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        month = month_map[m.group(2).lower()]
        return f"{year}-{month:02d}"

    # 4. Metrics MXT pattern: _YYMM (e.g. _2605)
    mxt_match = re.search(r'_(\d{2})(\d{2})', filename)
    if mxt_match:
        year = 2000 + int(mxt_match.group(1))
        month = int(mxt_match.group(2))
        return f"{year}-{month:02d}"

    return None

def check_gaps(time_series: List[Dict[str, Any]], fund_id: str) -> None:
    if not time_series:
        raise ValueError(f"Time series data is empty for {fund_id}.")
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
            raise ValueError(
                f"GAP DETECTED: Gap found in time-series between {time_series[i-1]['date']} and {time_series[i]['date']}."
            )

def parse_stake(fund_dir):
    manifest_path = os.path.join(fund_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found for Stake Accumulate: {manifest_path}")
        
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    raw_data_points = []
    
    for file_info in manifest.get("files", []):
        local_path = file_info.get("local_path")
        if not local_path or not local_path.endswith(".pdf"):
            continue
            
        pdf_path = os.path.join(fund_dir, local_path)
        print(f"Parsing Stake PDF: {local_path}")
        
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                registry = yaml.safe_load(f)
            max_pages = registry.get("stake_accumulate", {}).get("max_pdf_pages", None)

            with fitz.open(pdf_path) as doc:
                pages_to_read = min(max_pages, len(doc)) if max_pages else len(doc)
                p1_text = doc[0].get_text() if pages_to_read > 0 else ""
                p2_text = doc[1].get_text() if pages_to_read > 1 else ""
            
            p1_clean = clean_spacing(p1_text)
            p2_clean = clean_spacing(p2_text)
            
            # Find the report date from first occurrence of month + year on Page 1
            date_match = re.search(
                r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s*\d{4}\b',
                p1_clean,
                re.IGNORECASE
            )
            
            if not date_match:
                print(f"Warning: Could not parse date from PDF {local_path}. Skipping.")
                continue
                
            matched_str = date_match.group(0)
            month_name = date_match.group(1).lower()
            year = int(re.search(r'\d{4}', matched_str).group(0))
            month_num = MONTH_MAP[month_name]
            
            report_date = get_last_day_of_month(year, month_num)
            date_str = report_date.strftime("%Y-%m-%d")
            
            # Extract return from commentary using flat-text matching
            flat_text = "".join((p1_text + " " + p2_text).split()).lower()
            
            net_return = None
            # Prioritize matching "returned <val>% during <month>"
            pattern = r'returned(-?[0-9.]+)%(?:[a-z()]*)during' + month_name
            specific_match = re.search(pattern, flat_text)
            if specific_match:
                net_return = float(specific_match.group(1)) / 100.0
            else:
                # Fallback to the first "returned <val>%" in flat text
                all_matches = re.findall(r'returned(-?[0-9.]+)%', flat_text)
                if all_matches:
                    net_return = float(all_matches[0]) / 100.0
                    
            if net_return is None:
                print(f"Warning: Could not parse net return from PDF {local_path}. Skipping.")
                continue
                
            raw_data_points.append({
                "date": date_str,
                "net_return": net_return,
                "nav": 1.0,  # Placeholder, will be computed cumulatively
                "leverage_ratio": 1.0
            })
            
        except Exception as e:
            print(f"Error parsing Stake PDF {local_path}: {e}", file=sys.stderr)
            
    if not raw_data_points:
        raise ValueError("No data points parsed for Stake Accumulate.")
        
    # Sort chronologically
    raw_data_points.sort(key=lambda x: x["date"])
    
    
    # Compute cumulative NAV
    # Start base month at NAV = 1.0 (one month before first data point)
    first_date_parts = [int(p) for p in raw_data_points[0]["date"].split("-")]
    first_date = datetime.date(first_date_parts[0], first_date_parts[1], first_date_parts[2])
    # Base date is one month before first_date
    if first_date.month == 1:
        base_date = get_last_day_of_month(first_date.year - 1, 12)
    else:
        base_date = get_last_day_of_month(first_date.year, first_date.month - 1)
        
    time_series = [
        {
            "date": base_date.strftime("%Y-%m-%d"),
            "net_return": 0.0,
            "nav": 1.0,
            "leverage_ratio": 1.0
        }
    ]
    
    current_nav = 1.0
    for dp in raw_data_points:
        current_nav = current_nav * (1.0 + dp["net_return"])
        time_series.append({
            "date": dp["date"],
            "net_return": dp["net_return"],
            "commentary_truth": dp.get("commentary_truth"),
            "nav": current_nav,
            "leverage_ratio": dp["leverage_ratio"]
        })
        
    # Latest month
    latest_month = time_series[-1]["date"][:7] # YYYY-MM
    
    output_data = {
        "fund_id": "stake_accumulate",
        "fund_name": "Stake Accumulate",
        "apir_code": "NO_APIR",
        "scraped_at": datetime.datetime.now().isoformat(),
        "source_url": manifest["source_url"],
        "data_period": latest_month,
        "time_series": time_series
    }
    
    return latest_month, output_data

def _process_single_bentham_pdf(pdf_path: str, local_path: str, max_pages: Optional[int]) -> Optional[Dict[str, Any]]:
    print(f"Parsing Bentham PDF: {local_path}")
    try:
        text = ""
        with fitz.open(pdf_path) as doc:
            pages_to_read = min(max_pages, len(doc)) if max_pages else len(doc)
            for i in range(pages_to_read):
                text += doc[i].get_text()
        text = clean_spacing(text)
        
        # Regex extraction rules
        date_match = re.search(r'(?:Fund Performance as at \d+|fact sheet\s*–)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text, re.IGNORECASE)
        if not date_match:
            date_match = re.search(r'(?:Fund Performance as at \d+|fact sheet\s*–)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', text, re.IGNORECASE)
        if not date_match:
            date_match = re.search(r'Bentham(?: Wholesale)? Global Income Fund(?: Monthly)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text, re.IGNORECASE)
        if not date_match:
            date_match = re.search(r'Bentham(?: Wholesale)? Global Income Fund(?: Monthly)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', text, re.IGNORECASE)

        if not date_match:
            print(f"Warning: Could not parse date from PDF {local_path}. Skipping.")
            return None

        month_name = date_match.group(1).lower()
        year = int(date_match.group(2))
        if month_name == 'sept': month_name = 'sep'
        month_num = MONTH_MAP[month_name]
        
        report_date = get_last_day_of_month(year, month_num)
        date_str = report_date.strftime("%Y-%m-%d")

        # 1. Primary Source: Commentary exact text
        commentary_return = None
        comm_match = re.search(r'total return\s*\(after fees\*?\)\s*of\s*(-?[0-9.]+)\s*(?:%|percent)', text, re.IGNORECASE)
        if comm_match:
            commentary_return = float(comm_match.group(1)) / 100.0

        # 2. Secondary Source: Table extraction (Robust)
        table_return = None
        ret_match = re.search(r'Total return \(after fees\)(.*?)(?:Benchmark|$)', text, re.IGNORECASE)
        if ret_match:
            numbers = ret_match.group(1).split()
            # Find the first valid number. Ignore stray '1' or '2' footnotes.
            for num_str in numbers:
                # Strip potential trailing/leading chars safely
                clean_num = re.sub(r'[^0-9.-]', '', num_str)
                if clean_num in ('1', '2', '3') and len(num_str) <= 2:
                    continue  # Skip stray footnote digits
                
                # Deal with welding manually if the footnote stuck to a minus sign: e.g. "1-9.30"
                if clean_num.startswith('1-') or clean_num.startswith('2-'):
                    clean_num = clean_num[1:]
                
                try:
                    table_return = float(clean_num) / 100.0
                    break
                except ValueError:
                    continue

        # Reconcile sources
        net_return = commentary_return
        if net_return is None:
            # Fallback to table if commentary is missing (very old formats)
            net_return = table_return
        else:
            # If both exist and differ significantly, we log it and keep commentary
            if table_return is not None and abs(net_return - table_return) > 1e-4:
                # This suggests the table extraction picked up the wrong column (e.g. 3 Months instead of 1 Month)
                print(f"Warning: Commentary ({net_return*100}%) and Table ({table_return*100}%) differ for {local_path}. Trusting commentary.")

        if net_return is None:
            raise ValueError(f"CRITICAL DATA GAP: Could not parse net return from PDF {local_path}.")

        return {
            "date": date_str,
            "net_return": net_return,
            "commentary_truth": commentary_return,
            "nav": 1.0,
            "leverage_ratio": 1.0
        }
    except Exception as e:
        print(f"Error parsing Bentham PDF {local_path}: {e}", file=sys.stderr)
        return None

def parse_bentham(fund_dir: str) -> Tuple[str, Dict[str, Any]]:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    max_pages = registry.get("bentham_global_income_fund", {}).get("max_pdf_pages", None)

    manifest_path = os.path.join(fund_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found for Bentham Global Income Fund: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Load cache if exists
    cache_path = os.path.join(fund_dir, "history_cache.json")
    cache_data = None
    existing_months = set()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                # Skip baseline month at index 0
                existing_months = {
                    dp["date"][:7] for idx, dp in enumerate(cache_data.get("time_series", []))
                    if idx > 0
                }
        except Exception as e:
            print(f"Warning: Failed to load history cache: {e}", file=sys.stderr)

    tasks = []
    for file_info in manifest.get("files", []):
        local_path = file_info.get("local_path")
        if not local_path or not local_path.endswith(".pdf"):
            continue
        filename = os.path.basename(local_path)
        month_prefix = extract_month_prefix(filename)
        if month_prefix and month_prefix in existing_months:
            continue
        pdf_path = os.path.join(fund_dir, local_path)
        tasks.append((pdf_path, local_path, max_pages))

    # If no new files and cache exists, return cache directly
    if cache_data and not tasks:
        latest_month = cache_data.get("data_period")
        if not latest_month:
            latest_month = cache_data["time_series"][-1]["date"][:7]
        print("Bentham: No new files to parse. Returning cached data.")
        return latest_month, cache_data

    raw_data_points = []
    if len(tasks) == 1:
        # Synchronous execution
        res = _process_single_bentham_pdf(*tasks[0])
        if res:
            raw_data_points.append(res)
    elif len(tasks) > 1:
        # Multi-process execution
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_single_bentham_pdf, *task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        raw_data_points.append(res)
                except Exception as e:
                    print(f"Error parsing Bentham PDF: {e}", file=sys.stderr)

    if not raw_data_points and not (cache_data and cache_data.get("time_series")):
        raise ValueError("No data points parsed for Bentham Global Income Fund.")

    # Merge newly parsed data points with cached data points
    cached_points = []
    if cache_data:
        cached_points = cache_data.get("time_series", [])[1:]  # Skip baseline month

    merged_points_map = {dp["date"]: dp for dp in cached_points}
    for dp in raw_data_points:
        dp_copy = {
            "date": dp["date"],
            "net_return": dp["net_return"],
            "commentary_truth": dp.get("commentary_truth"),
            "leverage_ratio": dp.get("leverage_ratio", 1.0)
        }
        merged_points_map[dp["date"]] = dp_copy

    merged_data_points = list(merged_points_map.values())
    if not merged_data_points:
        raise ValueError("No data points parsed or cached for Bentham Global Income Fund.")

    merged_data_points.sort(key=lambda x: x["date"])

    first_date_parts = [int(p) for p in merged_data_points[0]["date"].split("-")]
    first_date = datetime.date(first_date_parts[0], first_date_parts[1], first_date_parts[2])
    if first_date.month == 1:
        base_date = get_last_day_of_month(first_date.year - 1, 12)
    else:
        base_date = get_last_day_of_month(first_date.year, first_date.month - 1)

    time_series = [
        {"date": base_date.strftime("%Y-%m-%d"), "net_return": 0.0, "nav": 1.0, "leverage_ratio": 1.0}
    ]

    current_nav = 1.0
    for dp in merged_data_points:
        current_nav = current_nav * (1.0 + dp["net_return"])
        time_series.append({
            "date": dp["date"],
            "net_return": dp["net_return"],
            "commentary_truth": dp.get("commentary_truth"),
            "nav": current_nav,
            "leverage_ratio": dp["leverage_ratio"]
        })

    # Validate for gaps in merged time-series
    check_gaps(time_series, "bentham_global_income_fund")

    latest_month = time_series[-1]["date"][:7]
    output_data = {
        "fund_id": "bentham_global_income_fund",
        "fund_name": "Bentham Global Income Fund",
        "apir_code": "CSA0038AU",
        "scraped_at": datetime.datetime.now().isoformat(),
        "source_url": manifest["source_url"],
        "data_period": latest_month,
        "time_series": time_series
    }

    # Save to history_cache.json
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    return latest_month, output_data

def parse_coolabah(fund_id, fund_dir):
    manifest_path = os.path.join(fund_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found for {fund_id}: {manifest_path}")
        
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    html_path = os.path.join(fund_dir, "report.html")
    if not os.path.exists(html_path):
        raise FileNotFoundError(f"HTML report file not found: {html_path}")
        
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Find Plotly JSON data specifically for cumulative performance
    plotly_data = None
    fund_trace = None
    for script in soup.find_all('script', type='application/json'):
        try:
            data = json.loads(script.text)
            if "x" in data and "data" in data["x"]:
                for trace in data["x"]["data"]:
                    if trace.get("type") == "scatter" and len(trace.get("x", [])) > 20:
                        # Check if any element in text contains a dollar sign
                        text_list = trace.get("text", [])
                        if text_list and any('$' in str(item) for item in text_list):
                            if "RBA" not in trace.get("name", ""):
                                plotly_data = data
                                fund_trace = trace
                                break
                if fund_trace:
                    break
        except Exception:
            continue
            
    if not plotly_data or not fund_trace:
        raise ValueError(f"Could not locate growth chart Plotly JSON payload for Coolabah {fund_id}.")
        
    # 3. Parse date and cumulative values from text array
    raw_points = []
    for item in fund_trace.get("text", []):
        match = re.search(r'(\d{4}-\d{2}-\d{2}):\s*\$([0-9.,]+)', item)
        if match:
            date_str = match.group(1)
            nav = float(match.group(2).replace(',', ''))
            raw_points.append({
                "date": date_str,
                "nav": nav
            })
            
    if not raw_points:
        raise ValueError(f"No performance points extracted from Plotly trace for {fund_id}.")
        
    # Sort chronologically
    raw_points.sort(key=lambda x: x["date"])
    
    # 4. Compute monthly returns
    time_series = [
        {
            "date": raw_points[0]["date"],
            "net_return": 0.0,
            "nav": 1.0,  # Base scale at 1.0
            "leverage_ratio": 1.0
        }
    ]
    
    base_val = raw_points[0]["nav"]
    for i in range(1, len(raw_points)):
        prev_val = raw_points[i-1]["nav"]
        curr_val = raw_points[i]["nav"]
        net_return = (curr_val - prev_val) / prev_val
        time_series.append({
            "date": raw_points[i]["date"],
            "net_return": net_return,
            "nav": curr_val / base_val,
            "leverage_ratio": 1.0  # Default to unlevered, metrics will update if leverage is available
        })
        
    latest_month = time_series[-1]["date"][:7] # YYYY-MM
    
    # Load registry details for official names and APIR
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    fund_info = registry[fund_id]
    
    output_data = {
        "fund_id": fund_id,
        "fund_name": fund_info.get("fund_name"),
        "apir_code": fund_info.get("apir_code"),
        "scraped_at": datetime.datetime.now().isoformat(),
        "source_url": manifest["source_url"],
        "data_period": latest_month,
        "time_series": time_series
    }

    return latest_month, output_data


def _process_single_metrics_pdf(pdf_path: str, local_path: str) -> List[Dict[str, Any]]:
    print(f"Parsing Metrics PDF: {local_path}")
    try:
        with fitz.open(pdf_path) as doc:
            if len(doc) < 2:
                raise ValueError(f"Metrics PDF {local_path} has fewer than 2 pages.")
            p1_text = doc[0].get_text()
            p2_blocks = doc[1].get_text("blocks")

        p1_clean = clean_spacing(p1_text)

        # Find report date on Page 1 (e.g. MAY 2026)
        date_match = re.search(
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s*(\d{4})\b',
            p1_clean,
            re.IGNORECASE
        )
        if not date_match:
            # Fallback to short month names
            date_match = re.search(
                r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*(\d{4})\b',
                p1_clean,
                re.IGNORECASE
            )

        if not date_match:
            raise ValueError(f"Could not parse report date from Page 1 of {local_path}")

        month_name = date_match.group(1).lower()
        year = int(date_match.group(2))
        if month_name == 'sept': month_name = 'sep'
        month_num = MONTH_MAP[month_name]

        raw_data_points = []
        for b in p2_blocks:
            block_text = b[4].strip()
            lines = [line.strip() for line in block_text.split('\n') if line.strip()]
            if not lines:
                continue

            # Check if this block represents a year of returns (e.g. starting with a year 2017-2026)
            if re.match(r'^\d{4}$', lines[0]):
                block_year = int(lines[0])
                if 2017 <= block_year <= 2026:
                    # Restrict Net Return table y-coordinate bounding box [110, 283]
                    y0 = b[1]
                    if 110 <= y0 <= 283:
                        # Remove year (first element) and YTD (last element)
                        values = lines[1:-1]

                        # Map values to months
                        if block_year == 2017:
                            # IPO October 2017: values should contain 3 elements corresponding to Oct, Nov, Dec
                            if len(values) == 3:
                                months = [10, 11, 12]
                            else:
                                months = list(range(13 - len(values), 13))
                        elif block_year == year:
                            # Current year: values correspond to Jan up to month_num
                            months = list(range(1, len(values) + 1))
                        else:
                            # Normal full year: 12 months
                            months = list(range(1, 13))

                        for idx, val in enumerate(values):
                            if idx >= len(months):
                                break
                            m = months[idx]
                            try:
                                # Deal with percentage value
                                net_return = float(val) / 100.0
                                m_date = get_last_day_of_month(block_year, m)
                                raw_data_points.append({
                                    "date": m_date.strftime("%Y-%m-%d"),
                                    "net_return": net_return,
                                    "nav": 1.0,
                                    "leverage_ratio": 1.0
                                })
                            except ValueError:
                                # Skip if not a float
                                continue

        return raw_data_points
    except Exception as e:
        print(f"Error parsing Metrics PDF {local_path}: {e}", file=sys.stderr)
        return []

def parse_metrics(fund_dir: str) -> Tuple[str, Dict[str, Any]]:
    manifest_path = os.path.join(fund_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found for Metrics: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Find all downloaded PDF files
    pdf_files = []
    for file_info in manifest.get("files", []):
        local_path = file_info.get("local_path")
        if local_path and local_path.endswith(".pdf"):
            pdf_files.append(local_path)

    if not pdf_files:
        raise ValueError("No PDF files found for Metrics Master Income Trust.")

    # Find the latest PDF by parsing the YYMM pattern in filename (e.g. 2605 - MXT Monthly Report.pdf)
    def extract_yymm(filename: str) -> int:
        match = re.search(r'_(\d{2})(\d{2})', filename)
        if match:
            return int(match.group(1)) * 100 + int(match.group(2))
        return 0

    pdf_files.sort(key=extract_yymm, reverse=True)

    # Load cache if exists
    cache_path = os.path.join(fund_dir, "history_cache.json")
    cache_data = None
    existing_months = set()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                # Skip baseline month at index 0
                existing_months = {
                    dp["date"][:7] for idx, dp in enumerate(cache_data.get("time_series", []))
                    if idx > 0
                }
        except Exception as e:
            print(f"Warning: Failed to load history cache for Metrics: {e}", file=sys.stderr)

    tasks = []
    for local_path in pdf_files:
        filename = os.path.basename(local_path)
        month_prefix = extract_month_prefix(filename)
        if month_prefix and month_prefix in existing_months:
            continue
        pdf_path = os.path.join(fund_dir, local_path)
        tasks.append((pdf_path, local_path))

    # If no new files and cache exists, return cache directly
    if cache_data and not tasks:
        latest_month = cache_data.get("data_period")
        if not latest_month:
            latest_month = cache_data["time_series"][-1]["date"][:7]
        print("Metrics: No new files to parse. Returning cached data.")
        return latest_month, cache_data

    raw_data_points = []
    if len(tasks) == 1:
        # Synchronous execution
        res = _process_single_metrics_pdf(*tasks[0])
        if res:
            raw_data_points.extend(res)
    elif len(tasks) > 1:
        # Multi-process execution
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_single_metrics_pdf, *task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        raw_data_points.extend(res)
                except Exception as e:
                    print(f"Error parsing Metrics PDF: {e}", file=sys.stderr)

    if not raw_data_points and not (cache_data and cache_data.get("time_series")):
        raise ValueError("Could not parse any Net Return data points for MXT.")

    # Merge newly parsed data points with cached data points
    cached_points = []
    if cache_data:
        cached_points = cache_data.get("time_series", [])[1:]  # Skip baseline month

    merged_points_map = {dp["date"]: dp for dp in cached_points}
    for dp in raw_data_points:
        dp_copy = {
            "date": dp["date"],
            "net_return": dp["net_return"],
            "commentary_truth": dp.get("commentary_truth") if dp.get("commentary_truth") is not None else dp["net_return"],
            "leverage_ratio": dp.get("leverage_ratio", 1.0)
        }
        merged_points_map[dp["date"]] = dp_copy

    merged_data_points = list(merged_points_map.values())
    if not merged_data_points:
        raise ValueError("No data points parsed or cached for Metrics Master Income Trust.")

    merged_data_points.sort(key=lambda x: x["date"])

    if len(merged_data_points) < 36:
        raise ValueError(f"Insufficient data: only {len(merged_data_points)} months parsed (minimum 36).")

    # Build chronological time series with baseline month
    first_date_parts = [int(p) for p in merged_data_points[0]["date"].split("-")]
    first_date = datetime.date(first_date_parts[0], first_date_parts[1], first_date_parts[2])

    if first_date.month == 1:
        base_date = get_last_day_of_month(first_date.year - 1, 12)
    else:
        base_date = get_last_day_of_month(first_date.year, first_date.month - 1)

    time_series = [
        {
            "date": base_date.strftime("%Y-%m-%d"),
            "net_return": 0.0,
            "nav": 1.0,
            "leverage_ratio": 1.0
        }
    ]

    current_nav = 1.0
    for dp in merged_data_points:
        current_nav = current_nav * (1.0 + dp["net_return"])
        time_series.append({
            "date": dp["date"],
            "net_return": dp["net_return"],
            "commentary_truth": dp.get("commentary_truth") if dp.get("commentary_truth") is not None else dp["net_return"],
            "nav": current_nav,
            "leverage_ratio": dp["leverage_ratio"]
        })

    # Validate for gaps in merged time-series
    check_gaps(time_series, "metrics_master_income_trust")

    latest_month = time_series[-1]["date"][:7]

    # Load registry details for official name and APIR
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    fund_info = registry["metrics_master_income_trust"]

    output_data = {
        "fund_id": "metrics_master_income_trust",
        "fund_name": fund_info.get("fund_name"),
        "apir_code": fund_info.get("apir_code"),
        "scraped_at": datetime.datetime.now().isoformat(),
        "source_url": manifest["source_url"],
        "data_period": latest_month,
        "time_series": time_series
    }

    # Save to history_cache.json
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    return latest_month, output_data


def main():
    parser = argparse.ArgumentParser(description="Parse raw Australian Fixed Income Fund factsheets/HTML into structured raw JSON.")
    parser.add_argument("--fund", required=True, help="Fund ID to parse (e.g. stake_accumulate)")
    args = parser.parse_args()
    
    fund_id = args.fund
    print(f"=== Starting parsing for fund: {fund_id} ===")
    
    fund_dir = os.path.join(BASE_DIR, "data", "raw", fund_id)
    if not os.path.exists(fund_dir):
        print(f"CRITICAL: Raw data directory not found: {fund_dir}. Please run fetch_web.py first.", file=sys.stderr)
        sys.exit(1)
        
    try:
        if fund_id == "stake_accumulate":
            latest_month, data = parse_stake(fund_dir)
        elif fund_id == "bentham_global_income_fund":
            latest_month, data = parse_bentham(fund_dir)
        elif fund_id == "metrics_master_income_trust":
            latest_month, data = parse_metrics(fund_dir)
        else:
            latest_month, data = parse_coolabah(fund_id, fund_dir)
            
        # Save output structured json in data/raw/{fund_id}/{latest_month}.json
        output_file = os.path.join(fund_dir, f"{latest_month}.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        print(f"SUCCESS: Successfully parsed and saved structured raw data to {output_file}")
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL: Factsheet parsing failed for {fund_id}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
