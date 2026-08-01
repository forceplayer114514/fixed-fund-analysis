"""Spec E verify.py 单测.

覆盖:
  - check_quote_tokens: 空 quote / 空 doc / token 不在 quote / 不在 doc / 全过
  - _flatten: PyMuPDF 空格怪癖归一化
  - check_rolling: 十进制单位, 缺失即放行, 有值+历史充足 → 对上/对不上
  - check_field_type: |v| < 0.5 十进制
  - check_anti_fabrication: 连续 3 个非零同值判 fail
  - _prev_yms: 跨年
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.verify import (
    _flatten,
    _prev_yms,
    check_anti_fabrication,
    check_field_type,
    check_fund_name_token,
    check_quote_tokens,
    check_rolling,
)


# ---------- _flatten (PyMuPDF 空格归一化) ----------

def test_flatten_percent_space():
    assert _flatten("0.65 %") == "0.65%"


def test_flatten_dollar_space():
    assert _flatten("$ 1023") == "$1023"


def test_flatten_paren_space():
    assert _flatten("( 0.45 )") == "(0.45)"


def test_flatten_dot_space():
    assert _flatten("0. 65") == "0.65"


def test_flatten_comma_space():
    assert _flatten("1, 023") == "1,023"


def test_flatten_minus_space():
    assert _flatten("- 0.45") == "-0.45"


def test_flatten_multi_whitespace():
    assert _flatten("A   B\n\tC") == "A B C"


def test_flatten_empty():
    assert _flatten("") == ""


# ---------- check_quote_tokens: 空 case ----------

def test_no_tokens_passes_not_found():
    """not_found 场景 tokens 为空, 视 pass."""
    r = check_quote_tokens([], "", "")
    assert r.passed
    assert r.reason == "no_tokens_to_check"


def test_empty_source_quote_fails():
    r = check_quote_tokens(["0.65%"], "", "doc has 0.65%")
    assert not r.passed
    assert "empty_source_quote" in r.reason


def test_empty_doc_text_fails():
    r = check_quote_tokens(["0.65%"], "quote 0.65%", "")
    assert not r.passed
    assert "empty_doc_text" in r.reason


# ---------- check_quote_tokens: 正常 ----------

def test_all_tokens_match_passes():
    tokens = ["0.65%", "1.85%"]
    quote = "Net Return (%) 0.65 1.85"  # 归一化后含 0.65% 1.85% 吗? 否 — 数字与 % 之间要相邻
    # 更好: 用真实 quote 格式
    quote = "Net Return (%): 0.65%, 3M: 1.85%"
    doc = "Fund Report ... Net Return (%): 0.65%, 3M: 1.85% ..."
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed, r.reason


def test_token_missing_from_quote_fails():
    """LLM 编 quote (缺 token)."""
    tokens = ["0.65%"]
    r = check_quote_tokens(tokens, "quote without value", "doc has 0.65%")
    assert not r.passed
    assert "token_not_in_quote" in r.reason


def test_token_missing_from_doc_fails():
    """LLM 编数据 (quote 里有 token 但 doc 没有 — 幻觉)."""
    tokens = ["0.65%"]
    r = check_quote_tokens(tokens, "quote 0.65%", "doc has 9.99%")
    assert not r.passed
    assert "token_not_in_doc" in r.reason


def test_pymupdf_space_tolerated():
    """PDF 抽的 doc_text 常带空格, 归一化后应匹配."""
    tokens = ["0.65%"]
    quote = "Net Return: 0.65%"
    doc = "Net Return :\n0. 65 %"  # PyMuPDF 常见空格
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed, r.reason


def test_nav_pair_tokens_both_in_quote():
    """nav_pair 场景 2 NAV 都在 quote 里."""
    tokens = ["$134.24", "$135.16"]
    quote = "FRHY<br />2026-05-31: $134.24, FRHY<br />2026-06-30: $135.16"
    doc = quote  # HTML 直接就是 doc
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed


# ---------- check_fund_name_token: 独立闸, 不要求在 source_quote 里 ----------

def test_fund_name_token_none_passes():
    """fund_name_text 未转写 (None) -> pass, 不阻塞."""
    r = check_fund_name_token(None, "some doc text")
    assert r.passed
    assert r.reason == "no_fund_name_text"


def test_fund_name_token_found_in_doc_passes():
    """基金名出现在抬头, 不要求在 source_quote (跟数值行不是同一段落)."""
    doc = "Stake Accumulate Fund\nMonthly Report\n...\nNet Return (%) 0.65"
    r = check_fund_name_token("Stake Accumulate Fund", doc)
    assert r.passed, r.reason


def test_fund_name_token_hallucinated_fails():
    """文档里根本没这个名字 -> 幻觉, 判 fail."""
    r = check_fund_name_token("Totally Made Up Fund", "Net Return (%) 0.65")
    assert not r.passed
    assert "fund_name_not_in_doc" in r.reason


def test_fund_name_token_empty_doc_fails():
    r = check_fund_name_token("Stake Accumulate Fund", "")
    assert not r.passed
    assert "empty_doc_text" in r.reason


def test_fund_name_token_pymupdf_space_tolerated():
    """PDF 抽取常见空格怪癖 (数字/符号周边), 名字本身也走同一 _flatten 归一化."""
    r = check_fund_name_token("Stake Accumulate Fund",
                              "Stake  Accumulate   Fund\nNet Return")
    assert r.passed, r.reason


# ---------- check_rolling: 十进制 ----------

def test_rolling_no_report_passes_zero_windows():
    r = check_rolling(0.0065, "2026-05", {}, {})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "no_rolling_reported"


def test_rolling_all_none_passes():
    r = check_rolling(0.0065, "2026-05", {}, {"1mo": None, "3mo": None})
    assert r.passed
    assert r.windows_verified == 0


def test_rolling_insufficient_history_passes():
    r = check_rolling(0.0065, "2026-05", {}, {"3mo": 0.0185})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "insufficient_history"


def test_rolling_3mo_matches_decimal():
    """(1.0065) * (1.006) * (1.0059) - 1 ≈ 0.018463; report 0.0185 内 tol."""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    r = check_rolling(0.0065, "2026-05", history, {"3mo": 0.0185})
    assert r.passed
    assert r.windows_verified == 1


def test_rolling_mismatch_fails():
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    # net=0.018 (季度当月度), 3mo 复利 ≈ 3%, 与报 0.0185 差 >0.5%
    r = check_rolling(0.018, "2026-05", history, {"3mo": 0.0185})
    assert not r.passed
    assert "mismatch_3mo" in r.reason


def test_rolling_not_found_passes():
    r = check_rolling(None, "2026-05", {}, {"3mo": 0.0185})
    assert r.passed


def test_rolling_1mo_shifted_labels_discarded_not_rejected():
    """Stake 2026-07 教训回归: rolling 标签整体错位一格 (表格首列缺失导致
    LLM 把 3mo/6mo 的真实值错标成 1mo/3mo), 数值本身没错但跟 net_return 对不上
    -- 应整批弃用 rolling, 不拿错位数据去比对本来对的 net_return, 也不阻塞入库。

    真实案例: 2025-05 net_return=0.66%, rolling_text 报 {"1mo":"0.82%","3mo":"2.98%",
    "6mo":"2.98%"} (实为表格 3mo/6mo/inception 列错标)。0.82% 是真实的 3mo 值,
    跟复利算出的 implied 3mo(0.83%) 其实对得上, 但因为标签错位读的是 rolling["3mo"]
    (装的是 6mo 的 2.98%), 会被判 mismatch -- 自洽预检应先拦下, 不让它走到这步。
    """
    history = {"2025-04": 0.0068, "2025-03": -0.0051}
    r = check_rolling(0.0066, "2025-05", history,
                      {"1mo": 0.0082, "3mo": 0.0298, "6mo": 0.0298})
    assert r.passed
    assert r.windows_verified == 0
    assert "rolling_labels_shifted_discarded" in r.reason


def test_rolling_1mo_matches_net_return_proceeds_to_real_check():
    """1mo 与 net_return 对得上 (标签没错位) 时, 3mo/6mo 真实校验照常跑, 行为不变。"""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    r = check_rolling(0.0065, "2026-05", history,
                      {"1mo": 0.0065, "3mo": 0.0185})
    assert r.passed
    assert r.windows_verified == 1


def test_rolling_3mo_6mo_duplicate_values_discarded_not_rejected():
    """Stake 2026-07 二次复现: 这次 1mo 读对了 (等于 net_return, 自洽预检不拦),
    但 3mo/6mo 报出完全相同的 2.98% (implied_3mo 复算 0.83%, 跟这两个数都对不上)。
    不同复利窗口位位相同是损坏/缺失单元格数据被分摊到多个标签的信号, 不是
    "数值编不出来"的真错误 -- 该整批弃用这两个窗口, 不阻塞入库。

    真实案例 (2025-05): net_return=0.66%, history 2025-04=0.68%/2025-03=-0.51%,
    rolling_text {"1mo":"0.66%","3mo":"2.98%","6mo":"2.98%"}。
    """
    history = {"2025-04": 0.0068, "2025-03": -0.0051}
    r = check_rolling(0.0066, "2025-05", history,
                      {"1mo": 0.0066, "3mo": 0.0298, "6mo": 0.0298})
    assert r.passed
    assert r.windows_verified == 0
    assert "rolling_windows_duplicated_discarded" in r.reason


def test_rolling_duplicate_check_only_discards_duplicated_window_others_still_checked():
    """3mo/6mo 重复被弃用, 但 12mo 数值独立且真实校验对得上时, 该窗口照常验证
    并计入 windows_verified (不是整批 rolling 一刀切弃用)。"""
    history = {
        "2025-04": 0.0068, "2025-03": -0.0051, "2025-02": 0.0040,
        "2025-01": 0.0035, "2024-12": 0.0030, "2024-11": 0.0025,
        "2024-10": 0.0020, "2024-09": 0.0015, "2024-08": 0.0010,
        "2024-07": 0.0005, "2024-06": 0.0060,
    }
    prevs_12 = ["2025-04", "2025-03", "2025-02", "2025-01", "2024-12", "2024-11",
                "2024-10", "2024-09", "2024-08", "2024-07", "2024-06"]
    product = 1.0066
    for pm in prevs_12:
        product *= 1.0 + history[pm]
    true_12mo = round(product - 1.0, 6)
    r = check_rolling(0.0066, "2025-05", history,
                      {"1mo": 0.0066, "3mo": 0.0298, "6mo": 0.0298, "12mo": true_12mo})
    assert r.passed
    assert r.windows_verified == 1
    assert "rolling_windows_duplicated_discarded" not in r.reason


def test_rolling_1mo_matches_but_3mo_genuinely_wrong_still_caught():
    """1mo 没错位, 但 3mo 数值本身真的对不上 (如字段类型错) -- 自洽预检不该
    误伤真实的字段错误检测, 该拦的还得拦。"""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    r = check_rolling(0.018, "2026-05", history,
                      {"1mo": 0.018, "3mo": 0.0185})
    assert not r.passed
    assert "mismatch_3mo" in r.reason


# ---------- _prev_yms ----------

def test_prev_yms_wraps_year():
    assert _prev_yms("2020-02", 3) == ["2020-01", "2019-12", "2019-11"]


def test_prev_yms_zero():
    assert _prev_yms("2026-05", 0) == []


# ---------- check_field_type ----------

def test_field_type_ok():
    assert check_field_type(0.0065)[0]


def test_field_type_rejects_annualized():
    ok, reason = check_field_type(0.65)  # 65% 月度 = 不可能
    assert not ok
    assert "out_of_range" in reason


def test_field_type_negative_ok():
    assert check_field_type(-0.05)[0]


def test_field_type_none_ok():
    assert check_field_type(None)[0]


# ---------- check_anti_fabrication ----------

def test_antifab_normal_passes():
    ok, _ = check_anti_fabrication(0.005, "2026-05",
                                   [("2026-04", 0.004), ("2026-03", 0.006)])
    assert ok


def test_antifab_rejects_run_of_3():
    hist = [("2026-04", 0.005), ("2026-03", 0.005)]
    ok, reason = check_anti_fabrication(0.005, "2026-05", hist)
    assert not ok
    assert "identical_run_3" in reason


def test_antifab_zero_run_allowed():
    hist = [("2026-04", 0.0), ("2026-03", 0.0)]
    ok, _ = check_anti_fabrication(0.0, "2026-05", hist)
    assert ok


def test_antifab_ignores_history_not_adjacent_to_ym():
    """回填场景: DB 已有全库最新月, 但当前 ym 是很久以前的历史月.

    hist 里 2026-05/2026-04 是同值 run, 但离 ym=2020-05 十万八千里,
    不应污染 2020-05 的连续性判定 -- 应看 2020-04/2020-03 是否同值。
    """
    hist = [("2026-05", 0.0042), ("2026-04", 0.0042), ("2026-03", 0.0042)]
    ok, _ = check_anti_fabrication(0.0042, "2020-05", hist)
    assert ok


def test_antifab_rejects_run_of_3_adjacent_to_ym_regardless_of_input_order():
    """recent_history 乱序传入 (非 sorted reverse) 也要能挡住紧邻 ym 的同值 run。"""
    hist = [("2019-11", 0.006), ("2020-04", 0.005), ("2020-03", 0.005)]
    ok, reason = check_anti_fabrication(0.005, "2020-05", hist)
    assert not ok
    assert "identical_run_3" in reason


class TestCheckFundIdentity:
    """Spec G 10.5: 逐份核对文档基金身份, 兄弟基金必须被拦下。"""

    def test_exact_same_name_passes(self):
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Yarra Enhanced Income Fund", "Yarra Enhanced Income Fund")
        assert r.passed

    def test_sibling_fund_is_rejected(self):
        """核心用例: 老的 _name_matches 判据在这里会放行 (双方去停用词后
        都只剩 {yarra}), 新判据必须拦下。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Yarra Australian Income Fund", "Yarra Enhanced Income Fund")
        assert not r.passed
        assert "identity_mismatch" in r.reason

    def test_structural_suffix_stripped(self):
        """份额类别等结构性后缀不影响身份判定。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Bentham Syndicated Loan Fund - Wholesale Class",
            "Bentham Syndicated Loan Fund")
        assert r.passed

    def test_etf_wrapper_word_stripped(self):
        """2026-08-01 实测: Coolabah 月报抬头是 "...High Yield Complex ETF",
        用户登记的名字没带 ETF, 17 个月全被拦成待审。ETF 与 fund/trust/class
        同属"这只产品用什么壳装的", 不是区分哪只基金的词。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Coolabah Global Floating-Rate High Yield Complex ETF",
            "Coolabah Global Floating-Rate High Yield Complex")
        assert r.passed, r.reason

    def test_etf_stripping_does_not_let_siblings_through(self):
        """剥掉 ETF 之后, 兄弟份额类别仍必须被拦下。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Coolabah Global Floating-Rate High Yield Fund AI",
            "Coolabah Global Floating-Rate High Yield Complex")
        assert not r.passed

    def test_user_typo_tolerated(self):
        """真实案例: pdf_cache 里存在 stake_accumlate 目录 (少一个 u)。
        用户输入拼写错误不应导致全部数据转待审。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Stake Accumulate Fund", "Stake Accumlate Fund")
        assert r.passed

    def test_different_issuer_rejected(self):
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Smarter Money Long-Short Credit Fund",
            "Coolabah Active Composite Bond Fund")
        assert not r.passed

    def test_empty_fund_name_text_does_not_block(self):
        """模型没读到抬头时不阻断 (与 check_fund_name_token 现有行为一致)。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(None, "Yarra Enhanced Income Fund")
        assert r.passed
        assert r.reason == "no_fund_name_text"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
