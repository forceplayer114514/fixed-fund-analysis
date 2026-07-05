import os
import sys
import argparse
import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup
import yaml
import json

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        raise FileNotFoundError(f"Fund registry file not found at {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def download_file(url, filepath):
    print(f"Downloading {url} to {filepath}...")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(resp.content)

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
    
    for idx, (text, pdf_url) in enumerate(pdf_links):
        filename = pdf_url.split('/')[-1]
        # Clean up filename if it has URL parameters
        filename = filename.split('?')[0]
        filepath = os.path.join(fund_dir, filename)
        
        # Download the PDF
        try:
            download_file(pdf_url, filepath)
            manifest["files"].append({
                "label": text,
                "url": pdf_url,
                "local_path": filename
            })
        except Exception as e:
            print(f"Warning: Failed to download {pdf_url}: {e}", file=sys.stderr)
            
    # Write manifest
    with open(os.path.join(fund_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        
    print("Stake Accumulate fetch completed.")

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
        else:
            fetch_coolabah(fund_id, confirmed_url, fund_dir)
        sys.exit(0)
    except Exception as e:
        print(f"CRITICAL: Fetch failed for {fund_id}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
