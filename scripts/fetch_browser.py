import os
import sys
import argparse

def main():
    parser = argparse.ArgumentParser(description="Browser harness fetching (Cloudflare fallback).")
    parser.add_argument("--fund", required=True, help="Fund ID")
    args = parser.parse_args()
    
    print(f"fetch_browser.py: Browser-harness fallback crawler invoked for {args.fund}.")
    print("Note: Currently requests+BeautifulSoup (fetch_web.py) is sufficient and preferred.")
    print("Bypassing browser-harness fetch.")
    sys.exit(0)

if __name__ == "__main__":
    main()
