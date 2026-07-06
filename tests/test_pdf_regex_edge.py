import pytest
import re

def parse_net_return(text):
    net_return = None
    ret_match = re.search(r'Total return \(after fees\)(.*?)(?:Benchmark|$)', text, re.IGNORECASE)
    if ret_match:
        numbers = ret_match.group(1).split()
        if numbers:
            num_str = numbers[0]
            if num_str == "1" and len(numbers) > 1:
                num_str = numbers[1]
            elif num_str.startswith("1-"):
                num_str = num_str[1:]
            elif num_str.startswith("1") and len(num_str) >= 3 and num_str[2] == ".":
                num_str = num_str[1:]
            try:
                net_return = float(num_str) / 100.0
            except ValueError:
                pass
    return net_return

@pytest.mark.unit
def test_legitimate_one_percent_return_no_footnote():
    # True return is 1.18%, no footnote. 
    # Decimal is at index 1, so the rule shouldn't strip the leading 1.
    text = "Total return (after fees) 1.18 -0.50 Benchmark"
    assert parse_net_return(text) == 0.0118

@pytest.mark.unit
def test_sticky_footnote_on_single_digit_return():
    # Sticky footnote 1 + return 1.32% = 11.32.
    # Decimal is at index 2, rule SHOULD strip leading 1.
    text = "Total return (after fees)11.32 -0.78 Benchmark"
    assert parse_net_return(text) == 0.0132
    
@pytest.mark.unit
def test_sticky_footnote_on_zero_point_return():
    # Sticky footnote 1 + return 0.95% = 10.95.
    text = "Total return (after fees)10.95 -0.78 Benchmark"
    assert parse_net_return(text) == 0.0095
    
@pytest.mark.unit
def test_sticky_footnote_on_negative_return():
    # Sticky footnote 1 + return -0.19% = 1-0.19.
    text = "Total return (after fees)1-0.19 -0.78 Benchmark"
    assert parse_net_return(text) == -0.0019

@pytest.mark.unit
def test_separated_footnote():
    text = "Total return (after fees) 1 -0.40 Benchmark"
    assert parse_net_return(text) == -0.0040

