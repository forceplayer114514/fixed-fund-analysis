import os
import sys
import argparse
import json
import datetime
import requests
from bs4 import BeautifulSoup
import re

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def fetch_rba_cash_rate():
    print("Fetching current RBA Cash Rate from official website...")
    url = "https://www.rba.gov.au/"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        h = soup.find(string=lambda x: x and 'Cash rate target' in x)
        if not h:
            raise ValueError("Could not find 'Cash rate target' text on RBA page.")
        parent = h.find_parent('article') or h.find_parent('div')
        if not parent:
            raise ValueError("Could not find parent container of 'Cash rate target' element.")
        val_el = parent.find(class_='statistic-value')
        if not val_el:
            raise ValueError("Could not find element with class 'statistic-value' inside cash rate container.")
        
        val_text = val_el.text.strip()
        match = re.search(r'[0-9.]+', val_text)
        if not match:
            raise ValueError(f"Could not parse numeric cash rate from text: '{val_text}'")
            
        rate = float(match.group(0)) / 100.0
        print(f"RBA Cash Rate successfully scraped: {rate * 100.0:.2f}%")
        return rate
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to scrape RBA Cash Rate: {e}", file=sys.stderr)
        raise

def fetch_historical_cash_rates(current_rate):
    cache_path = os.path.join(BASE_DIR, "data", "rba_cash_rate_history.json")
    if os.path.exists(cache_path):
        try:
            mtime = os.path.getmtime(cache_path)
            age = datetime.datetime.now().timestamp() - mtime
            if age < 86400: # 1 day cache
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to read cash rate history cache: {e}")

    print("Fetching historical RBA Cash Rate from DBnomics API...")
    url = "https://api.db.nomics.world/v22/series/RBA/F1/FIRMMCRTD?observations=1"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        doc = data['series']['docs'][0]
        
        rates = {}
        for period, val in zip(doc['period'], doc['value']):
            if val == 'NA' or val is None:
                continue
            try:
                val_float = float(val) / 100.0
            except ValueError:
                continue
            rates[period[:7]] = val_float # 'YYYY-MM' -> rate
            
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(rates, f, indent=2)
            
        return rates
    except Exception as e:
        print(f"Warning: Failed to fetch cash rate history from DBnomics: {e}")
        if os.path.exists(cache_path):
            print("Using cached cash rate history as fallback.")
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        # If no cache exists, initialize with current rate for all months
        print("No cache found. Falling back to constant current rate.")
        return {}

def calculate_autocorrelation(returns: list[float], fund_name: str = "Unknown") -> tuple[float, float]:
    n = len(returns)
    if n < 2:
        return 0.0, 0.0
    mean_r = sum(returns) / n
    numerator = sum((returns[t] - mean_r) * (returns[t-1] - mean_r) for t in range(1, n))
    denominator = sum((r - mean_r) ** 2 for r in returns)
    if denominator == 0:
        return 0.0, 0.0
    phi = numerator / denominator
    q_stat = n * (n + 2) * (phi ** 2) / (n - 1)
    return phi, q_stat

def calculate_downside_deviation_excess(excess_returns: list[float], fund_name: str = "Unknown") -> float:
    n = len(excess_returns)
    if n == 0:
        return 0.0
    squared_negative = [r ** 2 if r < 0 else 0.0 for r in excess_returns]
    mean_squared = sum(squared_negative) / n
    return (mean_squared * 12) ** 0.5

def calculate_max_drawdown(nav_series: list[float], fund_name: str = "Unknown") -> float:
    if not nav_series:
        return 0.0
    max_dd = 0.0
    peak = nav_series[0]
    for nav in nav_series:
        if nav > peak:
            peak = nav
        if peak < 1e-4:
            raise ValueError(f"[{fund_name}] peak={peak} (低于 1e-4)，无法计算最大回撤，请检查该基金的 NAV 数据是否异常")
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd

def calculate_annualized_return(compounded_return: float, n_months: int, fund_name: str = "Unknown") -> float:
    if n_months <= 0:
        raise ValueError(f"[{fund_name}] n_months={n_months}, 无法计算年化收益率，时间序列月份数必须为正数")
    return (compounded_return ** (12.0 / n_months)) - 1.0

def calculate_annualized_volatility(returns: list[float], fund_name: str = "Unknown") -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean_r = sum(returns) / n
    denominator = n - 1
    if denominator <= 0:
        raise ValueError(f"[{fund_name}] denominator={denominator} (n_months - 1), 无法计算年化波动率")
    variance = sum((r - mean_r) ** 2 for r in returns) / denominator
    return (variance * 12.0) ** 0.5

def calculate_sortino_ratio(annualized_excess_return: float, downside_deviation: float, fund_name: str = "Unknown", field_name: str = "downside_deviation") -> float:
    if downside_deviation < 1e-6:
        raise ValueError(f"[{fund_name}] {field_name}={downside_deviation} (低于下限 1e-6)，无法计算Sortino比率，请检查该基金是否存在异常平稳的收益率序列")
    return annualized_excess_return / downside_deviation

def unsmooth_returns(returns: list[float], phi: float, fund_name: str = "Unknown") -> list[float]:
    if not returns:
        return []
    denom = 1.0 - phi
    if denom < 0.01:
        raise ValueError(f"[{fund_name}] phi={phi} 导致分母 1 - phi 为 {denom} (低于下限 0.01)，无法进行 Geltner 去平滑计算")
    unsmoothed_returns = [returns[0]]
    for t in range(1, len(returns)):
        unsmoothed_val = (returns[t] - phi * returns[t-1]) / denom
        unsmoothed_returns.append(unsmoothed_val)
    return unsmoothed_returns

def main():
    parser = argparse.ArgumentParser(description="Calculate portfolio comparison metrics for Australian Fixed Income Funds.")
    parser.add_argument("--fund", required=True, help="Fund ID (e.g. stake_accumulate)")
    parser.add_argument("--date", required=True, help="Data period (e.g. 2026-05)")
    args = parser.parse_args()
    
    fund_id = args.fund
    date_period = args.date
    
    print(f"=== Calculating Metrics for {fund_id} ({date_period}) ===")
    
    cleaned_file = os.path.join(BASE_DIR, "data", "cleaned", fund_id, f"{date_period}.validated.json")
    if not os.path.exists(cleaned_file):
        print(f"CRITICAL: Cleaned validated JSON not found at {cleaned_file}. Please run validate_data.py first.", file=sys.stderr)
        sys.exit(1)
        
    # Get current RBA Cash Rate
    try:
        current_rba_rate = fetch_rba_cash_rate()
    except Exception:
        sys.exit(1) # Fail-fast gate
        
    # Get historical cash rates
    historical_rates = fetch_historical_cash_rates(current_rba_rate)
        
    with open(cleaned_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    time_series = data["time_series"]
    
    # Extract historical returns and dates (excluding base month return at index 0)
    data_points = time_series[1:]
    returns = [dp["net_return"] for dp in data_points]
    dates = [dp["date"] for dp in data_points] # List of 'YYYY-MM'

    # Also extract full NAV series for max drawdown (including index 0)
    nav_series = [dp["nav"] for dp in time_series]
    max_dd = calculate_max_drawdown(nav_series, fund_name=data.get("fund_name", fund_id))

    n_months = len(returns)
    print(f"History length: {n_months} months.")
    
    is_short_warning = n_months < 6
    
    # Resolve RBA Cash Rate for each historical month
    fund_rf_rates = []
    for d in dates:
        # Check historical dictionary, fallback to current rate
        # Date key is YYYY-MM-DD, slice to YYYY-MM to match rba_cash_rate_history keys
        rate = historical_rates.get(d[:7], current_rba_rate)
        fund_rf_rates.append(rate)
    
    fund_name = data.get("fund_name", fund_id)

    # 1. Unsmoothing (Geltner)
    phi = 0.0
    q_stat = 0.0
    is_geltner_applied = False
    unsmoothed_returns = list(returns)

    if n_months >= 36:
        phi, q_stat = calculate_autocorrelation(returns, fund_name=fund_name)
        is_significant = q_stat > 3.841
        print(f"Estimated phi: {phi:.4f}, Q-stat: {q_stat:.4f} (Significant: {is_significant})")

        if is_significant and (0.0 <= phi <= 0.85):
            print(f"Geltner unsmoothing applied with phi={phi:.4f}")
            is_geltner_applied = True
            unsmoothed_returns = unsmooth_returns(returns, phi, fund_name=fund_name)
        else:
            print("Geltner unsmoothing skipped (phi negative, too high, or not significant).")
    else:
        print("Geltner unsmoothing skipped due to short history (< 36 months).")
        
    # Calculate monthly excess returns (Fund return - benchmark rate / 12)
    # Original Excess Returns
    excess_returns_original = [r - (rf / 12.0) for r, rf in zip(returns, fund_rf_rates)]
    # Unsmoothed Excess Returns
    excess_returns_unsmoothed = [r - (rf / 12.0) for r, rf in zip(unsmoothed_returns, fund_rf_rates)]
        
    # 2. Compounded Annualized Returns
    # Original Absolute
    comp_orig_abs = 1.0
    for r in returns:
        comp_orig_abs *= (1.0 + r)
    ann_return_original = calculate_annualized_return(comp_orig_abs, n_months, fund_name=fund_name)

    # Compound Annualized RBA Benchmark
    comp_rba = 1.0
    for rf in fund_rf_rates:
        comp_rba *= (1.0 + rf / 12.0)
    ann_rba = calculate_annualized_return(comp_rba, n_months, fund_name=fund_name)

    # Original Excess (Difference between Fund and RBA annualized returns)
    ann_excess_return_original = ann_return_original - ann_rba

    # Unsmoothed Absolute
    comp_un_abs = 1.0
    for r in unsmoothed_returns:
        comp_un_abs *= (1.0 + r)
    ann_return_unsmoothed = calculate_annualized_return(comp_un_abs, n_months, fund_name=fund_name)

    # Unsmoothed Excess (Difference between Unsmoothed Fund and RBA annualized returns)
    ann_excess_return_unsmoothed = ann_return_unsmoothed - ann_rba
    
    # 3. Annualized Volatility
    ann_vol_original = calculate_annualized_volatility(returns, fund_name=fund_name)
    ann_vol_unsmoothed = calculate_annualized_volatility(unsmoothed_returns, fund_name=fund_name)
    ann_vol_excess_original = calculate_annualized_volatility(excess_returns_original, fund_name=fund_name)
    ann_vol_excess_unsmoothed = calculate_annualized_volatility(excess_returns_unsmoothed, fund_name=fund_name)

    # 4. Sortino Ratio (using excess returns and downside deviation of excess returns relative to 0)
    downside_original = calculate_downside_deviation_excess(excess_returns_original, fund_name=fund_name)
    sortino_original = calculate_sortino_ratio(
        ann_excess_return_original,
        downside_original,
        fund_name=fund_name,
        field_name="downside_deviation_original"
    )

    downside_unsmoothed = calculate_downside_deviation_excess(excess_returns_unsmoothed, fund_name=fund_name)
    sortino_unsmoothed = calculate_sortino_ratio(
        ann_excess_return_unsmoothed,
        downside_unsmoothed,
        fund_name=fund_name,
        field_name="downside_deviation_unsmoothed"
    )
    
    # ponytail: Removed credit spread decomposition and leverage contribution calculations.
    # We no longer calculate credit spread or leverage effects since leverage ratio data cannot be parsed reliably.

    metrics_result = {
        "fund_id": fund_id,
        "fund_name": data["fund_name"],
        "apir_code": data["apir_code"],
        "data_period": date_period,
        "rba_cash_rate": current_rba_rate,
        "history_months": n_months,
        "is_short_history_warning": is_short_warning,
        "unsmoothing_coefficient_phi": phi,
        "is_geltner_applied": is_geltner_applied,
        "original_metrics": {
            "annualized_return": ann_return_original,
            "annualized_excess_return": ann_excess_return_original,
            "annualized_volatility": ann_vol_original,
            "annualized_volatility_excess": ann_vol_excess_original,
            "sortino_ratio": sortino_original,
            "downside_deviation": downside_original,
            "max_drawdown": max_dd
        },
        "unsmoothed_metrics": {
            "annualized_return": ann_return_unsmoothed,
            "annualized_excess_return": ann_excess_return_unsmoothed,
            "annualized_volatility": ann_vol_unsmoothed,
            "annualized_volatility_excess": ann_vol_excess_unsmoothed,
            "sortino_ratio": sortino_unsmoothed,
            "downside_deviation": downside_unsmoothed,
            "max_drawdown": max_dd
        }
    }
    
    output_file = os.path.join(BASE_DIR, "data", "cleaned", fund_id, f"{date_period}.metrics.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(metrics_result, f, indent=2)
        
    print(f"SUCCESS: Metrics calculated and saved to {output_file}")
    sys.exit(0)

if __name__ == "__main__":
    main()
