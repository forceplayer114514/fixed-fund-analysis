"""PDF 页裁剪 (喂模型) + get_text (校验用)."""
from __future__ import annotations

from pathlib import Path
from typing import Union

import fitz  # PyMuPDF

DEFAULT_MAX_PAGES = 2


def clip_pages(path: Union[str, Path], max_pages: int = DEFAULT_MAX_PAGES) -> bytes:
    """抽取前 N 页成新 PDF, 返回 bytes.

    max_pages <=0 或超过总页数 -> 取全文.
    """
    doc = fitz.open(str(path))
    total = doc.page_count
    if max_pages is None or max_pages <= 0 or max_pages >= total:
        return doc.tobytes()
    out = fitz.open()
    out.insert_pdf(doc, from_page=0, to_page=max_pages - 1)
    return out.tobytes()


def full_text(path: Union[str, Path]) -> str:
    """PyMuPDF 全文抽取, 用于 quote 校验 (不用于喂模型)."""
    doc = fitz.open(str(path))
    return "\n".join(page.get_text() for page in doc)


def page_count(path: Union[str, Path]) -> int:
    doc = fitz.open(str(path))
    return doc.page_count
