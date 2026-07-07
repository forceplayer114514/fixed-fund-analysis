import os
import sys
import argparse
import datetime
import urllib.parse
import re
import json
import asyncio
from typing import List, Tuple, Set

import requests
from bs4 import BeautifulSoup
import yaml
import aiohttp

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def load_cache_dates(fund_id: str) -> Set[str]:
    cache_path = os.path.join(BASE_DIR, "data", "raw", fund_id, "history_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {dp["date"] for dp in data.get("time_series", [])}
        except Exception:
            pass
    return set()

def filter_pdf_links(pdf_links: List[Tuple[str, str]], existing_dates: Set[str], fund_id: str) -> List[Tuple[str, str]]:
    filtered = []
    for text, url in pdf_links:
        filename = url.split('/')[-1]

        # 1. Look for YYYYMMD or YYYYMMDD (e.g. 20170131-GIF-Monthly-Report.pdf or 2023031-GIF-Monthly-Report.pdf)
        date_match = re.search(r'(\b|[^0-9])(\d{4})(\d{2})(\d{1,2})(\b|[^0-9])', filename)
        if date_match:
            year = int(date_match.group(2))
            month = int(date_match.group(3))
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue

        # 2. Look for YYYYMM (e.g. GIF-Monthly-Report-202502.pdf)
        date_match_short = re.search(r'(\b|[^0-9])(\d{4})(\d{2})(\b|[^0-9])', filename)
        if date_match_short:
            year = int(date_match_short.group(2))
            month = int(date_match_short.group(3))
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue

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
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue

        m = re.search(r"(\d{4})[-_]*(" + month_names_pattern + r")", filename, re.IGNORECASE)
        if m:
            year = int(m.group(1))
            month = month_map[m.group(2).lower()]
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue

        # 4. Metrics MXT pattern: _YYMM (e.g. _2605)
        mxt_match = re.search(r'_(\d{2})(\d{2})', filename)
        if mxt_match:
            year = 2000 + int(mxt_match.group(1))
            month = int(mxt_match.group(2))
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue

        filtered.append((text, url))
    return filtered

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        raise FileNotFoundError(f"Fund registry file not found at {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

async def download_file_async(session, url, filepath):
    print(f"Downloading {url} to {filepath}...")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    try:
        async with session.get(url, headers=HEADERS, timeout=20) as resp:
            resp.raise_for_status()
            content = await resp.read()
            with open(filepath, "wb") as f:
                f.write(content)
            return True, url, filepath
    except Exception as e:
        return False, url, str(e)

async def download_all_files(pdf_links, fund_dir):
    async with aiohttp.ClientSession() as session:
        tasks = []
        manifest_files = []
        for text, pdf_url in pdf_links:
            filename = pdf_url.split('/')[-1].split('?')[0]
            filepath = os.path.join(fund_dir, filename)
            tasks.append(download_file_async(session, pdf_url, filepath))
            manifest_files.append({
                "label": text,
                "url": pdf_url,
                "local_path": filename
            })

        results = await asyncio.gather(*tasks)

        # Filter manifest_files to only those that succeeded
        successful_manifest = []
        for i, (success, url, result) in enumerate(results):
            if success:
                successful_manifest.append(manifest_files[i])
            else:
                print(f"Warning: Failed to download {url}: {result}", file=sys.stderr)

        return successful_manifest

def fetch_stake(confirmed_url, fund_dir):
    print(f"Fetching Stake Accumulate page: {confirmed_url}")
    resp = requests.get(confirmed_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # Save the main HTML page
    html_path = os.path.join(fund_dir, "page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(resp.text)

    # Find all monthly report PDF links
    soup = BeautifulSoup(resp.text, 'html.parser')
    pdf_links = []
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        absolute_url = urllib.parse.urljoin(confirmed_url, href)
        if "accumulate" in absolute_url.lower() and (".pdf" in absolute_url.lower() or ".pdf?" in absolute_url.lower()):
            text = a.text.strip().replace('\n', ' ')
            pdf_links.append((text, absolute_url))

    if not pdf_links:
        raise ValueError("No monthly report PDF links found on Stake Accumulate page.")

    print(f"Found {len(pdf_links)} monthly report PDF links.")

    manifest = {
        "fund_id": "stake_accumulate",
        "fetched_at": datetime.datetime.now().isoformat(),
        "source_url": confirmed_url,
        "files": []
    }

    # Run async downloads
    successful_files = asyncio.run(download_all_files(pdf_links, fund_dir))
    manifest["files"] = successful_files

    # Write manifest
    with open(os.path.join(fund_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("Stake Accumulate fetch completed.")

def fetch_bentham(confirmed_url, fund_dir):
    print(f"Fetching Bentham Global Income Fund page: {confirmed_url}")
    # Bentham factsheets are typically located in the fund-reports page
    reports_url = "https://www.benthamam.com.au/funds/fund-reports/"
    resp = requests.get(reports_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # Save the main HTML page
    html_path = os.path.join(fund_dir, "page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(resp.text)

    soup = BeautifulSoup(resp.text, 'html.parser')
    pdf_links = []

    # We want to match all 'GIF-Monthly-Report' or 'GIF' + 'Monthly' links that don't have 'NZ'
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        absolute_url = urllib.parse.urljoin(reports_url, href)
        if "gif" in absolute_url.lower() and "monthly" in absolute_url.lower() and ".pdf" in absolute_url.lower() and "nz" not in absolute_url.lower():
            text = a.text.strip().replace('\n', ' ')
            if not text:
                text = "Bentham Global Income Fund Monthly Report"
            pdf_links.append((text, absolute_url))

    # deduplicate links
    unique_links = []
    seen = set()
    for text, url in pdf_links:
        if url not in seen:
            seen.add(url)
            unique_links.append((text, url))

    pdf_links = unique_links

    if not pdf_links:
        raise ValueError("No monthly report PDF links found on Bentham reports page.")

    print(f"Found {len(pdf_links)} monthly report PDF links.")

    # Filter out links that already exist in history cache
    existing_dates = load_cache_dates("bentham_global_income_fund")
    pdf_links = filter_pdf_links(pdf_links, existing_dates, "bentham_global_income_fund")
    print(f"Filtered to {len(pdf_links)} new monthly report PDF links for download.")

    manifest = {
        "fund_id": "bentham_global_income_fund",
        "fetched_at": datetime.datetime.now().isoformat(),
        "source_url": reports_url,
        "files": []
    }

    # Run async downloads
    successful_files = asyncio.run(download_all_files(pdf_links, fund_dir))
    manifest["files"] = successful_files

    # Write manifest
    with open(os.path.join(fund_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("Bentham Global Income Fund fetch completed.")


def fetch_metrics(confirmed_url, fund_dir):
    print(f"Fetching Metrics Master Income Trust page: {confirmed_url}")
    resp = requests.get(confirmed_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # Save the main HTML page
    html_path = os.path.join(fund_dir, "page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(resp.text)

    soup = BeautifulSoup(resp.text, 'html.parser')
    pdf_links = []

    # Find all monthly report PDF links matching MXT
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        absolute_url = urllib.parse.urljoin(confirmed_url, href)
        if "mxt" in absolute_url.lower() and "monthly" in absolute_url.lower() and "report" in absolute_url.lower() and (".pdf" in absolute_url.lower() or ".pdf?" in absolute_url.lower()):
            text = a.text.strip().replace('\n', ' ')
            if not text:
                text = "Metrics Master Income Trust Monthly Report"
            pdf_links.append((text, absolute_url))

    # deduplicate links
    unique_links = []
    seen = set()
    for text, url in pdf_links:
        if url not in seen:
            seen.add(url)
            unique_links.append((text, url))

    pdf_links = unique_links

    if not pdf_links:
        raise ValueError("No monthly report PDF links found on Metrics page.")

    print(f"Found {len(pdf_links)} monthly report PDF links.")

    # Filter out links that already exist in history cache
    existing_dates = load_cache_dates("metrics_master_income_trust")
    pdf_links = filter_pdf_links(pdf_links, existing_dates, "metrics_master_income_trust")
    print(f"Filtered to {len(pdf_links)} new monthly report PDF links for download.")

    manifest = {
        "fund_id": "metrics_master_income_trust",
        "fetched_at": datetime.datetime.now().isoformat(),
        "source_url": confirmed_url,
        "files": []
    }

    # Run async downloads
    successful_files = asyncio.run(download_all_files(pdf_links, fund_dir))
    manifest["files"] = successful_files

    # Write manifest
    with open(os.path.join(fund_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("Metrics Master Income Trust fetch completed.")


def fetch_coolabah(fund_id, confirmed_url, fund_dir):
    print(f"Fetching Coolabah {fund_id} page: {confirmed_url}")
    resp = requests.get(confirmed_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    
    # Save HTML page
    html_path = os.path.join(fund_dir, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(resp.text)
        
    # Find the print-friendly PDF link
    soup = BeautifulSoup(resp.text, 'html.parser')
    pdf_url = None
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        absolute_url = urllib.parse.urljoin(confirmed_url, href)
        if "performance-pdf" in absolute_url.lower() or (("performance" in absolute_url.lower()) and absolute_url.endswith(".pdf")):
            pdf_url = absolute_url
            break
            
    manifest = {
        "fund_id": fund_id,
        "fetched_at": datetime.datetime.now().isoformat(),
        "source_url": confirmed_url,
        "files": [
            {
                "label": "HTML Performance Page",
                "url": confirmed_url,
                "local_path": "report.html"
            }
        ]
    }
    
    if pdf_url:
        print(f"Found PDF report link: {pdf_url}")
        pdf_path = os.path.join(fund_dir, "report.pdf")
        try:
            download_file(pdf_url, pdf_path)
            manifest["files"].append({
                "label": "PDF Performance Report",
                "url": pdf_url,
                "local_path": "report.pdf"
            })
        except Exception as e:
            print(f"Warning: Failed to download PDF {pdf_url}: {e}", file=sys.stderr)
    else:
        print("Warning: No PDF report link found on Coolabah page.")
        
    # Write manifest
    with open(os.path.join(fund_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"Coolabah {fund_id} fetch completed.")

def main():
    parser = argparse.ArgumentParser(description="Fetch monthly report files for Australian Fixed Income Funds.")
    parser.add_argument("--fund", required=True, help="Fund ID to fetch data for (e.g. stake_accumulate)")
    args = parser.parse_args()
    
    fund_id = args.fund
    print(f"=== Starting fetch for fund: {fund_id} ===")
    
    try:
        registry = load_registry()
    except Exception as e:
        print(f"CRITICAL: Failed to load registry: {e}", file=sys.stderr)
        sys.exit(1)
        
    if fund_id not in registry:
        print(f"CRITICAL: Fund '{fund_id}' not found in registry.", file=sys.stderr)
        sys.exit(1)
        
    fund_info = registry[fund_id]
    confirmed_url = fund_info.get("confirmed_url")
    if not confirmed_url:
        print(f"CRITICAL: No confirmed URL found in registry for {fund_id}. Please run discover_source.py first.", file=sys.stderr)
        sys.exit(1)
        
    fund_dir = os.path.join(BASE_DIR, "data", "raw", fund_id)
    os.makedirs(fund_dir, exist_ok=True)
    
    try:
        if fund_id == "stake_accumulate":
            fetch_stake(confirmed_url, fund_dir)
        elif fund_id == "bentham_global_income_fund":
            fetch_bentham(confirmed_url, fund_dir)
        elif fund_id == "metrics_master_income_trust":
            fetch_metrics(confirmed_url, fund_dir)
        else:
            fetch_coolabah(fund_id, confirmed_url, fund_dir)
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL: Fetch failed for {fund_id}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
