"""lib/regress.py golden 回归测试。

不触网、不跑真实 fitz:monkeypatch lib.regress.extract_pdf_one_candidates 为
按 (fund_id, ym) 查表的假提取器(仿 test_ingest.py 对 download_and_extract_parallel
的 mock 方式)。pdf_cache 目录里只需要真实存在的 .pdf 文件名供 _cached_months
glob 探测,内容不重要。
"""
from __future__ import annotations

import os

import pytest

from lib.candidates import ReturnCandidate
from lib.db import create_fund, upsert_monthly_return
from lib.regress import run_fund_regression, run_regression


def _rolling(one_mo=None, three=None):
    return {"1mo": one_mo, "3mo": three, "6mo": None, "12mo": None,
            "inception": None, "parse_error": False, "extractor_tag": "perf",
            "precision": 2}


def _one_candidate(value, tag="test_pattern"):
    return [ReturnCandidate(value=value, source_quote=f"test:{value}",
                            pattern_tag=tag, char_pos=0, priority=0)]


def _touch_cached_pdfs(monkeypatch, tmp_path, fund_id: str, yms: list[str]) -> None:
    """在隔离的 FUND_PDF_CACHE 下为该基金创建空占位 .pdf(供 glob 探测)。"""
    cache_root = tmp_path / "pdf_cache"
    monkeypatch.setenv("FUND_PDF_CACHE", str(cache_root))
    fund_dir = cache_root / fund_id
    fund_dir.mkdir(parents=True, exist_ok=True)
    for ym in yms:
        (fund_dir / f"{ym}.pdf").write_bytes(b"")


def _make_resolved_chain(monkeypatch, fund_id: str):
    """三月链 r1=0.01/r2=0.02/r3=0.03,月月单候选 + 自证 1mo + m3 的 3mo 窗口。

    Pass Z 直接按唯一候选定值(不依赖约束传播);m3 的 3mo 窗口 + 各月 1mo
    自证窗口凑够 >=2 passed windows -> resolved。返回 presets 供 fake 提取器用。
    """
    r1, r2, r3 = 0.01, 0.02, 0.03
    three_mo = (1 + r1) * (1 + r2) * (1 + r3) - 1
    presets = {
        (fund_id, "2024-01"): (_one_candidate(r1), _rolling(one_mo=r1)),
        (fund_id, "2024-02"): (_one_candidate(r2), _rolling(one_mo=r2)),
        (fund_id, "2024-03"): (_one_candidate(r3), _rolling(one_mo=r3, three=three_mo)),
    }
    return presets, r3


def _patch_extractor(monkeypatch, presets: dict):
    def _fake(pdf_path, max_pages=None):
        fund_id = os.path.basename(os.path.dirname(pdf_path))
        ym = os.path.splitext(os.path.basename(pdf_path))[0]
        return presets[(fund_id, ym)]
    monkeypatch.setattr("lib.regress.extract_pdf_one_candidates", _fake)


@pytest.mark.unit
def test_no_cached_pdfs_no_regression(db_conn, monkeypatch, tmp_path):
    """基金未缓存任何 PDF(如 add-table/add-plotly 非 PDF 源)-> 不算回归。"""
    monkeypatch.setenv("FUND_PDF_CACHE", str(tmp_path / "empty_cache"))
    create_fund(db_conn, fund_id="no_pdf_fund", fund_name="No PDF Fund",
                confirmed_url="https://example.com", fetch_method="table",
                url_type="html")

    result = run_fund_regression(db_conn, "no_pdf_fund")
    assert result.months_checked == 0
    assert not result.has_regression
    assert result.value_drift == []
    assert result.coverage_regression == []


@pytest.mark.unit
def test_value_drift_detected(db_conn, monkeypatch, tmp_path):
    """库内 2024-03 值与本次 solver 重跑结果不同 -> value_drift。"""
    fund_id = "drift_fund"
    yms = ["2024-01", "2024-02", "2024-03"]
    _touch_cached_pdfs(monkeypatch, tmp_path, fund_id, yms)
    presets, true_r3 = _make_resolved_chain(monkeypatch, fund_id)
    _patch_extractor(monkeypatch, presets)

    create_fund(db_conn, fund_id=fund_id, fund_name="Drift Fund",
                confirmed_url="https://example.com", fetch_method="pdf_archive",
                url_type="pdf")
    # 库内 2024-03 是过时的错误值(0.05),本次重跑应得 true_r3=0.03
    upsert_monthly_return(db_conn, fund_id=fund_id, date="2024-03-31",
                          net_return=0.05)

    result = run_fund_regression(db_conn, fund_id)
    assert result.months_checked == 3
    assert result.has_regression is True
    drift_yms = {d["ym"] for d in result.value_drift}
    assert "2024-03" in drift_yms
    entry = next(d for d in result.value_drift if d["ym"] == "2024-03")
    assert entry["db_value"] == pytest.approx(0.05)
    assert entry["regress_value"] == pytest.approx(true_r3)
    assert result.coverage_regression == []


@pytest.mark.unit
def test_coverage_regression_detected(db_conn, monkeypatch, tmp_path):
    """库内有值,但本次重跑该月候选池为空(格式漂移致 no_candidates)-> 覆盖退化。"""
    fund_id = "gap_fund"
    yms = ["2024-01"]
    _touch_cached_pdfs(monkeypatch, tmp_path, fund_id, yms)
    presets = {
        (fund_id, "2024-01"): ([], {"1mo": None, "3mo": None, "6mo": None,
                                    "12mo": None, "inception": None,
                                    "parse_error": True, "extractor_tag": "none",
                                    "precision": 2}),
    }
    _patch_extractor(monkeypatch, presets)

    create_fund(db_conn, fund_id=fund_id, fund_name="Gap Fund",
                confirmed_url="https://example.com", fetch_method="pdf_archive",
                url_type="pdf")
    upsert_monthly_return(db_conn, fund_id=fund_id, date="2024-01-31",
                          net_return=0.02)

    result = run_fund_regression(db_conn, fund_id)
    assert result.has_regression is True
    assert len(result.coverage_regression) == 1
    entry = result.coverage_regression[0]
    assert entry["ym"] == "2024-01"
    assert entry["db_value"] == pytest.approx(0.02)
    assert entry["status"] == "no_candidates"
    assert result.value_drift == []


@pytest.mark.unit
def test_new_capability_not_regression(db_conn, monkeypatch, tmp_path):
    """本次重跑 resolved 但库内本就没有该月(未入库/pending)-> 仅提示,不算回归。"""
    fund_id = "new_fund"
    yms = ["2024-01", "2024-02", "2024-03"]
    _touch_cached_pdfs(monkeypatch, tmp_path, fund_id, yms)
    presets, true_r3 = _make_resolved_chain(monkeypatch, fund_id)
    _patch_extractor(monkeypatch, presets)

    create_fund(db_conn, fund_id=fund_id, fund_name="New Fund",
                confirmed_url="https://example.com", fetch_method="pdf_archive",
                url_type="pdf")
    # 不插入任何 monthly_returns 行

    result = run_fund_regression(db_conn, fund_id)
    assert result.has_regression is False
    new_yms = {d["ym"] for d in result.new_capability}
    assert "2024-03" in new_yms
    assert result.value_drift == []
    assert result.coverage_regression == []


@pytest.mark.unit
def test_run_regression_all_funds(db_conn, monkeypatch, tmp_path):
    """不传 fund_id 时遍历全库;含回归的基金令 report.has_regression=True。"""
    drift_id, clean_id = "drift_fund", "clean_fund"
    _touch_cached_pdfs(monkeypatch, tmp_path, drift_id, ["2024-01", "2024-02", "2024-03"])
    presets, true_r3 = _make_resolved_chain(monkeypatch, drift_id)

    clean_presets, _ = _make_resolved_chain(monkeypatch, clean_id)
    cache_root = tmp_path / "pdf_cache"
    fund_dir = cache_root / clean_id
    fund_dir.mkdir(parents=True, exist_ok=True)
    for ym in ["2024-01", "2024-02", "2024-03"]:
        (fund_dir / f"{ym}.pdf").write_bytes(b"")

    all_presets = {**presets, **clean_presets}
    _patch_extractor(monkeypatch, all_presets)

    create_fund(db_conn, fund_id=drift_id, fund_name="Drift Fund",
                confirmed_url="https://example.com", fetch_method="pdf_archive",
                url_type="pdf")
    upsert_monthly_return(db_conn, fund_id=drift_id, date="2024-03-31",
                          net_return=0.05)
    create_fund(db_conn, fund_id=clean_id, fund_name="Clean Fund",
                confirmed_url="https://example.com", fetch_method="pdf_archive",
                url_type="pdf")
    upsert_monthly_return(db_conn, fund_id=clean_id, date="2024-03-31",
                          net_return=true_r3)

    report = run_regression(db_conn)
    assert report.has_regression is True
    by_id = {f.fund_id: f for f in report.funds}
    assert by_id[drift_id].has_regression is True
    assert by_id[clean_id].has_regression is False
