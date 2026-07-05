import os
import sys
import argparse
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
import yaml
import re

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")
LOG_PATH = os.path.join(BASE_DIR, "references", "discovery_log.md")

# Default headers to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def log_attempt(message):
    print(message)
    # Check size of log file and rotate if it exceeds 1MB
    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1000000:
        bak_path = LOG_PATH + ".bak"
        try:
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(LOG_PATH, bak_path)
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"# URL Discovery Log (Rotated on {datetime.datetime.now().isoformat()})\n\n")
        except Exception as e:
            print(f"Warning: Failed to rotate log file: {e}", file=sys.stderr)
            
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)

def verify_url(url, fund_id, fund_name, apir_code):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        if resp.status_code != 200:
            resp.close()
            return False
            
        content_bytes = b""
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                content_bytes += chunk
            if len(content_bytes) >= 500 * 1024:
                break
        resp.close()
        
        content = content_bytes.decode('utf-8', errors='ignore')
        content_lower = content.lower()
        
        # Check title for 404
        title_text = ""
        title_match = re.search(r'<title>(.*?)</title>', content_lower)
        if title_match:
            title_text = title_match.group(1)
            if "404" in title_text and ("not found" in title_text or "page not found" in title_text or "error" in title_text):
                return False
        
        # Specific identifier checks to prevent cross-fund collisions
        check_str = (url.lower() + " " + title_text).lower()
        if fund_id == "coolabah_smarter_money":
            if "smarter" not in check_str or "money" not in check_str:
                return False
            if "long-short" in url.lower() or "higher-income" in url.lower():
                return False
        elif fund_id == "coolabah_long_short_credit":
            if "long" not in check_str or "short" not in check_str or "credit" not in check_str:
                return False
        elif fund_id == "coolabah_floating_rate_high_yield":
            if "floating" not in check_str or "rate" not in check_str or "high" not in check_str or "yield" not in check_str:
                return False
            if "global" in check_str:
                return False
        elif fund_id == "coolabah_long_short_opportunities":
            if "long" not in check_str or "short" not in check_str or "opportunities" not in check_str:
                return False
            if "credit" in check_str:
                return False
        elif fund_id == "stake_accumulate":
            pass
        else:
            # Fallback to generic name keywords check
            cleaned_name = fund_name.replace('(', '').replace(')', '').replace('-', ' ')
            for stop_word in ["fund", "class", "assisted", "direct", "investor"]:
                cleaned_name = re.sub(r'\b' + stop_word + r'\b', '', cleaned_name, flags=re.IGNORECASE)
            name_keywords = [kw.lower() for kw in cleaned_name.split() if len(kw) > 2]
            
            has_apir = (apir_code.lower() in content_lower or apir_code.lower() in url.lower()) if apir_code else False
            if not has_apir:
                matches = 0
                for kw in name_keywords:
                    if kw in content_lower or kw in url.lower():
                        matches += 1
                required_matches = min(2, len(name_keywords))
                if matches < required_matches:
                    return False

        # Topic signals
        topic_signals = ["performance", "report", "factsheet", "returns", "monthly", "nav"]
        if not any(sig in content_lower or sig in url.lower() for sig in topic_signals):
            return False
            
        # Strict context verification
        if fund_id == "stake_accumulate":
            soup = BeautifulSoup(content, 'html.parser')
            has_pdf = False
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                if ".pdf" in href and "accumulate" in href:
                    has_pdf = True
                    break
            if not has_pdf:
                return False
        elif "coolabah" in fund_id:
            # Must contain plotly widgets
            if "htmlwidgets" not in content_lower and "plotly" not in content_lower and "visdat" not in content_lower:
                return False
            
        return True
    except Exception:
        return False

def clean_yahoo_link(url):
    match = re.search(r'/RU=([^/]+)', url)
    if match:
        return urllib.parse.unquote(match.group(1))
    return url

def search_yahoo(query):
    url = "https://search.yahoo.com/search"
    params = {'p': query}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if "r.search.yahoo.com" in href:
                cleaned = clean_yahoo_link(href)
                if cleaned.startswith('http') and "yahoo.com" not in cleaned:
                    links.append(cleaned)
        return list(dict.fromkeys(links))
    except Exception as e:
        log_attempt(f"Yahoo search failed for query '{query}': {e}")
        return []

def search_ddg(query):
    url = "https://html.duckduckgo.com/html/"
    params = {'q': query}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        result_links = []
        for a in soup.find_all('a', class_='result__a'):
            href = a.get('href', '')
            if href:
                parsed = urllib.parse.urlparse(href)
                qs = urllib.parse.parse_qs(parsed.query)
                if 'uddg' in qs:
                    result_links.append(qs['uddg'][0])
                else:
                    result_links.append(href)
        return result_links
    except Exception as e:
        log_attempt(f"DDG search failed for query '{query}': {e}")
        return []

def search_ddg_lite(query):
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': query}
    try:
        resp = requests.post(url, data=data, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('http') and "duckduckgo.com" not in href:
                links.append(href)
        return list(dict.fromkeys(links))
    except Exception as e:
        log_attempt(f"DDG Lite search failed for query '{query}': {e}")
        return []

def search_web_engines(query):
    log_attempt(f"Searching Yahoo for: {query}")
    results = search_yahoo(query)
    if results:
        return results
    
    log_attempt(f"Searching DDG for: {query}")
    results = search_ddg(query)
    if results:
        return results

    log_attempt(f"Searching DDG Lite for: {query}")
    return search_ddg_lite(query)

def get_truncated_candidates(url):
    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    candidates = []
    if len(path_parts) > 1:
        # 1. Try removing one intermediate folder
        for i in range(len(path_parts) - 1):
            temp_parts = path_parts[:i] + path_parts[i+1:]
            new_path = '/' + '/'.join(temp_parts)
            candidates.append(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, new_path, '', '', '')))
        # 2. Try parent directories (backing out)
        for i in range(len(path_parts) - 1, 0, -1):
            new_path = '/' + '/'.join(path_parts[:i]) + '/'
            candidates.append(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, new_path, '', '', '')))
    return list(dict.fromkeys(candidates))

def parse_sitemap(domain_root, keywords):
    sitemap_url = urllib.parse.urljoin(domain_root, "/sitemap.xml")
    log_attempt(f"Checking sitemap: {sitemap_url}")
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        
        # Parse XML
        root = ET.fromstring(resp.content)
        # Handle namespaces
        namespace = ''
        if root.tag.startswith('{'):
            namespace = root.tag.split('}')[0] + '}'
            
        urls = []
        for url_node in root.findall(f'.//{namespace}loc'):
            url = url_node.text.strip()
            # Filter by keywords in URL path
            if any(kw.lower() in url.lower() for kw in keywords):
                urls.append(url)
        return urls
    except Exception as e:
        log_attempt(f"Sitemap parsing failed for {domain_root}: {e}")
        return []

def extract_links_from_page(parent_url, keywords):
    log_attempt(f"Extracting links from parent page: {parent_url}")
    try:
        resp = requests.get(parent_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            absolute_url = urllib.parse.urljoin(parent_url, href)
            # Basic validation
            if not absolute_url.startswith('http'):
                continue
            text = a.text.strip().lower()
            url_lower = absolute_url.lower()
            if any(kw.lower() in url_lower or kw.lower() in text for kw in keywords):
                links.append(absolute_url)
        return list(dict.fromkeys(links))
    except Exception as e:
        log_attempt(f"Failed to extract links from {parent_url}: {e}")
        return []

def check_wayback_machine(url):
    wayback_api = f"http://archive.org/wayback/available?url={url}"
    log_attempt(f"Checking Wayback Machine for: {url}")
    try:
        resp = requests.get(wayback_api, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            snapshots = data.get("archived_snapshots", {})
            if "closest" in snapshots and snapshots["closest"].get("available"):
                archive_url = snapshots["closest"]["url"]
                log_attempt(f"Found archive snapshot: {archive_url}")
                return archive_url
    except Exception as e:
        log_attempt(f"Wayback Machine lookup failed: {e}")
    return None

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        raise FileNotFoundError(f"Fund registry file not found at {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_registry(registry):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(registry, f, default_flow_style=False, allow_unicode=True)

def main():
    parser = argparse.ArgumentParser(description="Discover and verify Australian Fixed Income Fund report URLs.")
    parser.add_argument("--fund", required=True, help="Fund ID to discover sources for (e.g. stake_accumulate)")
    args = parser.parse_args()

    fund_id = args.fund
    log_attempt(f"=== Starting URL discovery for fund: {fund_id} ===")

    try:
        registry = load_registry()
    except Exception as e:
        log_attempt(f"CRITICAL: Failed to load registry: {e}")
        sys.exit(1)

    if fund_id not in registry:
        log_attempt(f"CRITICAL: Fund '{fund_id}' not found in registry.")
        sys.exit(1)

    fund_info = registry[fund_id]
    fund_name = fund_info.get("fund_name", fund_id)
    apir_code = fund_info.get("apir_code", "")
    fetch_method = fund_info.get("fetch_method", "requests+BeautifulSoup")

    # Define signals we expect to find on the page
    expected_signals = ["monthly", "report", "performance", "factsheet", "nav", "returns", apir_code]
    expected_signals = [sig for sig in expected_signals if sig]

    # Step 1: Check existing confirmed URL
    confirmed_url = fund_info.get("confirmed_url")
    if confirmed_url:
        log_attempt(f"Registry has existing URL: {confirmed_url}. Verifying...")
        if verify_url(confirmed_url, fund_id, fund_name, apir_code):
            log_attempt(f"SUCCESS: Existing URL is verified and active: {confirmed_url}")
            # Update verification date
            fund_info["verified_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
            fund_info["verification_signal"] = "Verified existing registry URL"
            save_registry(registry)
            sys.exit(0)
        else:
            log_attempt(f"Warning: Existing registry URL is dead/inactive: {confirmed_url}. Proceeding to active discovery.")
            fund_info["confirmed_url"] = ""

    # Active Discovery Process
    verified_url = None
    log_attempt(f"Starting active discovery steps for {fund_name} (APIR: {apir_code})")

    # Candidates pool
    candidates = []

    # Step 2: APIR search & Fund Name search on DDG
    queries = []
    if apir_code:
        queries.append(f'{apir_code} monthly performance report')
        queries.append(f'{apir_code} factsheet')
    queries.append(f'{fund_name} monthly performance report')
    queries.append(f'{fund_name} factsheet')

    for q in queries:
        search_results = search_web_engines(q)
        for link in search_results:
            if link not in candidates:
                candidates.append(link)

    log_attempt(f"Found {len(candidates)} search candidates to verify.")

    # Step 3: Run verification & path truncation on candidates
    verified_candidates = []
    
    # Filter candidates to only those containing fund name keywords or APIR code in the URL itself
    filtered_candidates = []
    cleaned_name = fund_name.replace('(', '').replace(')', '').replace('-', ' ')
    for stop_word in ["fund", "class", "assisted", "direct", "investor"]:
        cleaned_name = re.sub(r'\b' + stop_word + r'\b', '', cleaned_name, flags=re.IGNORECASE)
    name_keywords = [kw.lower() for kw in cleaned_name.split() if len(kw) > 2]
    
    for candidate in candidates:
        url_lower = candidate.lower()
        has_id = (apir_code.lower() in url_lower) if apir_code else False
        if not has_id:
            if any(kw in url_lower for kw in name_keywords):
                has_id = True
        if has_id:
            filtered_candidates.append(candidate)
            
    log_attempt(f"Filtered to {len(filtered_candidates)} relevant candidates out of {len(candidates)} total candidates.")

    for candidate in filtered_candidates:
        log_attempt(f"Verifying search candidate: {candidate}")
        if verify_url(candidate, fund_id, fund_name, apir_code):
            verified_url = candidate
            log_attempt(f"SUCCESS: Verified candidate URL: {verified_url}")
            break
        else:
            # Try Path Truncation Protocol
            log_attempt(f"Candidate failed verification. Trying path truncation protocol...")
            truncated_urls = get_truncated_candidates(candidate)
            for t_url in truncated_urls:
                log_attempt(f"Trying truncated path: {t_url}")
                if verify_url(t_url, fund_id, fund_name, apir_code):
                    verified_url = t_url
                    log_attempt(f"SUCCESS: Verified truncated URL: {verified_url}")
                    break
            if verified_url:
                break

    # Step 4: Sitemap & Parent Page crawling (if still not found)
    if not verified_url and candidates:
        # Extract unique domains from candidates to run sitemaps and parent pages
        domains = list(set([urllib.parse.urlunparse((urllib.parse.urlparse(c).scheme, urllib.parse.urlparse(c).netloc, '', '', '', '')) for c in candidates]))
        for dom in domains:
            # Try sitemap
            sitemap_urls = parse_sitemap(dom, ["performance", "monthly", "report", "factsheet"])
            for s_url in sitemap_urls:
                log_attempt(f"Verifying sitemap candidate: {s_url}")
                if verify_url(s_url, fund_id, fund_name, apir_code):
                    verified_url = s_url
                    log_attempt(f"SUCCESS: Verified URL from sitemap: {verified_url}")
                    break
            if verified_url:
                break
            
            # Try parent page crawling (e.g. going up to legal or support roots if found in candidates)
            parent_roots = [c for c in candidates if c.startswith(dom) and (('/legal' in c) or ('/support' in c) or ('/documents' in c) or ('/performance' in c) or ('/download' in c))]
            for p_root in parent_roots:
                # Go up to the section root
                section_url = p_root
                if p_root.endswith('.pdf'):
                    # Get folder
                    section_url = os.path.dirname(p_root) + '/'
                
                sub_links = extract_links_from_page(section_url, ["performance", "monthly", "report", "factsheet", apir_code])
                for sub_link in sub_links:
                    log_attempt(f"Verifying parent page link candidate: {sub_link}")
                    if verify_url(sub_link, fund_id, fund_name, apir_code):
                        verified_url = sub_link
                        log_attempt(f"SUCCESS: Verified URL from parent page: {verified_url}")
                        break
                if verified_url:
                    break
            if verified_url:
                break

    # Step 5: Wayback Machine check
    if not verified_url and confirmed_url:
        archive_url = check_wayback_machine(confirmed_url)
        if archive_url and verify_url(archive_url, fund_id, fund_name, apir_code):
            verified_url = archive_url
            log_attempt(f"SUCCESS: Verified URL from Wayback Machine: {verified_url}")

    # Finalization
    if verified_url:
        log_attempt(f"Discovery SUCCEEDED for {fund_id}. Saving to registry.")
        fund_info["confirmed_url"] = verified_url
        fund_info["verified_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
        fund_info["verification_signal"] = f"Automatically verified via discovery steps"
        save_registry(registry)
        sys.exit(0)
    else:
        log_attempt(f"CRITICAL: Discovery FAILED for {fund_id}. No verified URL could be found after trying all steps.")
        sys.exit(1)

if __name__ == "__main__":
    main()
