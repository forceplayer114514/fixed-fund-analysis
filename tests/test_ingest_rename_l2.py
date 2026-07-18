"""L2 (PDF/HTML/CSV per-month 循环) 自动纠名单测.

L1 fundmonitors 未覆盖时才走 L2; 循环内第一次拿到验证通过的 ex.fund_name_text
(check_fund_name_token 过 + fm_mod._name_matches 过) 触发一次 rename_fund_id,
之后沿用新 fund_id 跑完剩余月份。覆盖:
  - 正常触发 rename
  - fund_name_text 与用户输入名字面无关联 (_name_matches 拦) -> 不 rename
  - fund_name_text 不在文档原文里 (幻觉) -> check_fund_name_token 拦 -> 不 rename
  - pdf_cache 目录随 rename 物理搬迁 (真实文件系统, 不 mock)
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时 DB, 走既有 schema + 加 discovered_source_name (与
    test_ingest_priority_l1_l2.tmp_db 同构, 每个测试文件各自独立定义)."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("FUND_DB_PATH", tmp.name)
    conn = sqlite3.connect(tmp.name)
    from llm_ingest import store
    store.ensure_tables_if_missing(conn)
    conn.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _make_l2_req(fund_id="stake_fund", fund_name="Stake Fund"):
    from webapp.backend.app.schemas import IngestRequest
    return IngestRequest(
        fund_id=fund_id, fund_name=fund_name,
        issuer=None, confirmed_url="https://example.com/archive",
        issuer_domain=None, asx_code=None, apir_code=None,
        max_pdf_pages=None, limit=None,
    )


def _patch_l2_common(fake_ex):
    """L1 未覆盖 + discovery 产 1 条链 + 下载/提取全 mock 的公共 patch 集合.

    返回一个 contextlib.ExitStack 风格的 patch 列表, 调用方用 `with
    _patch_l2_common(...) :` 展开。
    """
    from llm_ingest import cli as llm_cli
    from llm_ingest import discover as disc_mod
    from llm_ingest import extract as ex_mod
    from llm_ingest import fundmonitors as fm
    from llm_ingest import pdf as pdf_mod

    return [
        patch.object(fm, "probe", return_value={
            "status": "no_fundid", "records": [], "ytd_map": {},
            "url": None, "page_fund_name": None, "errors": [],
        }),
        patch.object(disc_mod, "_fetch", return_value="<html>archive page</html>"),
        patch.object(disc_mod, "parse_archive_page", return_value=(
            [("2026-01", "https://issuer.example.com/2026-01.pdf")], False, "", [],
        )),
        patch.object(llm_cli, "_download_pdf", return_value=True),
        patch.object(ex_mod, "extract_from_pdf", return_value=fake_ex),
        patch.object(pdf_mod, "full_text", return_value=(
            "Stake Accumulate Fund\nMonthly Report\nNet Return (%) 0.65"
        )),
    ]


def _make_extraction(fund_name_text):
    from llm_ingest.extract import Extraction
    return Extraction(
        ym="2026-01", net_return=0.005, source_quote="",
        measure="table_value", measure_label_in_pdf="Net Return (%)",
        rolling={}, not_found=False, raw={},
        fund_name_text=fund_name_text,
    )


def test_l2_rename_on_fund_name_text(tmp_db):
    """fund_name_text 过两道检查 -> fund_id/fund_name 换成官方值, job 同步."""
    from webapp.backend.app.routers import ingest as ing

    req = _make_l2_req()
    fake_ex = _make_extraction("Stake Accumulate Fund")
    patches = _patch_l2_common(fake_ex)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        jid = ing._job_new(req.fund_id)
        ing._run_ingest_job(jid, req)

    assert ing._JOBS[jid]["fund_id"] == "stake_accumulate_fund"
    conn = sqlite3.connect(tmp_db)
    old_row = conn.execute("SELECT 1 FROM funds WHERE fund_id='stake_fund'").fetchone()
    new_row = conn.execute(
        "SELECT fund_name, discovered_source_name FROM funds WHERE fund_id='stake_accumulate_fund'"
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None
    assert new_row[0] == "Stake Accumulate Fund"
    assert new_row[1] == "Stake Accumulate Fund"


def test_l2_rename_not_triggered_on_name_mismatch(tmp_db):
    """fund_name_text 与用户输入名字面无重叠 (同页多基金混淆场景) -> 不 rename."""
    from webapp.backend.app.routers import ingest as ing

    req = _make_l2_req(fund_id="totally_unrelated_fund", fund_name="Totally Unrelated Fund")
    fake_ex = _make_extraction("Stake Accumulate Fund")
    patches = _patch_l2_common(fake_ex)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        jid = ing._job_new(req.fund_id)
        ing._run_ingest_job(jid, req)

    assert ing._JOBS[jid]["fund_id"] == "totally_unrelated_fund"
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT 1 FROM funds WHERE fund_id='totally_unrelated_fund'").fetchone()
    conn.close()
    assert row is not None


def test_l2_rename_not_triggered_when_hallucinated(tmp_db):
    """fund_name_text 不在文档原文里 (幻觉) -> check_fund_name_token 拦, 不 rename."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import pdf as pdf_mod

    req = _make_l2_req()
    fake_ex = _make_extraction("Stake Accumulate Fund")
    patches = _patch_l2_common(fake_ex)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        # full_text 覆盖成不含 fund_name_text 的文本 (幻觉场景)
        with patch.object(pdf_mod, "full_text", return_value="Net Return (%) 0.65, no fund name here"):
            jid = ing._job_new(req.fund_id)
            ing._run_ingest_job(jid, req)

    assert ing._JOBS[jid]["fund_id"] == "stake_fund"


def test_l2_pdf_cache_dir_renamed_with_real_files(tmp_db, tmp_path, monkeypatch):
    """rename 触发时, pdf_cache/{old_fund_id}/ 整个目录物理搬到
    pdf_cache/{new_fund_id}/ (真实文件系统, 不 mock Path.rename)."""
    from llm_ingest import cli as llm_cli
    from webapp.backend.app.routers import ingest as ing

    fake_pdf_root = tmp_path / "pdf_cache"
    monkeypatch.setattr(llm_cli, "PDF_ROOT", fake_pdf_root)

    old_dir = fake_pdf_root / "stake_fund"
    old_dir.mkdir(parents=True)
    (old_dir / "2026-01.pdf").write_bytes(b"%PDF-fake")

    req = _make_l2_req()
    fake_ex = _make_extraction("Stake Accumulate Fund")
    patches = _patch_l2_common(fake_ex)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        jid = ing._job_new(req.fund_id)
        ing._run_ingest_job(jid, req)

    assert ing._JOBS[jid]["fund_id"] == "stake_accumulate_fund"
    assert not old_dir.exists()
    new_dir = fake_pdf_root / "stake_accumulate_fund"
    assert new_dir.exists()
    assert (new_dir / "2026-01.pdf").read_bytes() == b"%PDF-fake"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
