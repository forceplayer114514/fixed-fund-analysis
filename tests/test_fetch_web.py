import sys
import os
import pytest
import re
from typing import List, Tuple, Set

# Add project root to python path to allow importing scripts
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.fetch_web import filter_pdf_links

@pytest.mark.unit
def test_filter_pdf_links_bentham() -> None:
    pdf_links = [
        ("GIF Jan 2017", "https://example.com/20170131-GIF-Monthly-Report.pdf"),
        ("GIF Feb 2017", "https://example.com/20170228-GIF-Monthly-Report.pdf"),
        ("GIF Mar 2023", "https://example.com/2023031-GIF-Monthly-Report.pdf"),
        ("GIF Feb 2025", "https://example.com/GIF-Monthly-Report-202502.pdf"),
        ("GIF Apr 2025", "https://example.com/GIF-Monthly-Report-April-2025.pdf"),
        ("GIF Nov 2024", "https://example.com/GIF-Monthly-Report-Nov-2024.pdf")
    ]
    existing_dates = {"2017-01-31", "2023-03-31", "2025-02-28", "2025-04-30", "2024-11-30"}
    filtered = filter_pdf_links(pdf_links, existing_dates, "bentham_global_income_fund")
    assert len(filtered) == 1
    assert "20170228" in filtered[0][1]

@pytest.mark.unit
def test_filter_pdf_links_metrics() -> None:
    pdf_links = [
        ("MXT May 2026", "https://example.com/6a34d33147df3603f998feea_2605%20-%20MXT%20Monthly%20Report.pdf"),
        ("MXT Apr 2026", "https://example.com/6a0faa9cfa58ddc24723c6c6_2604%20-%20MXT%20Monthly%20Report.pdf")
    ]
    existing_dates = {"2026-05-31"}
    filtered = filter_pdf_links(pdf_links, existing_dates, "metrics_master_income_trust")
    assert len(filtered) == 1
    assert "2604" in filtered[0][1]
