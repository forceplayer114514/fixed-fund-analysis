"""_rename_pdf_dir 单测 (2026-07 发现: 自动纠名把 old_dir rename 到 new_dir,
目标目录已存在且非空时原来直接崩 OSError: Directory not empty).
"""
from pathlib import Path

import pytest

from app.routers.ingest import _rename_pdf_dir


@pytest.mark.unit
def test_renames_when_target_does_not_exist(tmp_path):
    old_dir = tmp_path / "old_id"
    old_dir.mkdir()
    (old_dir / "2026-05.pdf").write_bytes(b"a")
    new_dir = tmp_path / "new_id"

    _rename_pdf_dir(old_dir, new_dir)

    assert not old_dir.exists()
    assert (new_dir / "2026-05.pdf").read_bytes() == b"a"


@pytest.mark.unit
def test_merges_into_existing_nonempty_target_without_crashing(tmp_path):
    """目标目录已存在且非空 (残留旧缓存/同名基金早年摄取) -- 不该抛 OSError."""
    old_dir = tmp_path / "old_id"
    old_dir.mkdir()
    (old_dir / "2026-05.pdf").write_bytes(b"new_content")

    new_dir = tmp_path / "new_id"
    new_dir.mkdir()
    (new_dir / "2019-01.pdf").write_bytes(b"pre_existing")

    _rename_pdf_dir(old_dir, new_dir)

    assert not old_dir.exists(), "old_dir 应被清空/移除, 不留半合并的残留"
    assert (new_dir / "2019-01.pdf").read_bytes() == b"pre_existing"
    assert (new_dir / "2026-05.pdf").read_bytes() == b"new_content"


@pytest.mark.unit
def test_same_filename_conflict_keeps_target_dirs_file(tmp_path):
    """同名文件冲突 -- 保留 new_dir 里已有的那份 (它是已确认对得上 new_id
    这支基金的缓存), 丢弃 old_dir 里的重复文件。"""
    old_dir = tmp_path / "old_id"
    old_dir.mkdir()
    (old_dir / "2026-05.pdf").write_bytes(b"from_old_dir")

    new_dir = tmp_path / "new_id"
    new_dir.mkdir()
    (new_dir / "2026-05.pdf").write_bytes(b"from_new_dir_already_confirmed")

    _rename_pdf_dir(old_dir, new_dir)

    assert not old_dir.exists()
    assert (new_dir / "2026-05.pdf").read_bytes() == b"from_new_dir_already_confirmed"
