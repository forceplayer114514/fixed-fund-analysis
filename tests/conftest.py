"""pytest conftest: 把 webapp/backend 加进 sys.path 让 test_ingest_priority_l1_l2
可 import `app.routers.ingest` 之类的 webapp 模块.

Spec D Phase E 修 pre-existing PYTHONPATH fail.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEBAPP_BACKEND = _REPO_ROOT / "webapp" / "backend"

for _p in (_REPO_ROOT, _WEBAPP_BACKEND):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


# ---------- classify_pdf_links 用的假 LLM ----------

# 清单一行一条 "<编号>. <url>"; url 里可能含空格 (Stake 真实文件名
# "Accumulate report_March2025.pdf" 未做 percent 编码), 故取到行尾而非 \S+
_LISTING_ITEM_RE = re.compile(r"^(\d+)\.\s+(.+)$", re.M)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class SelectStubClient:
    """假 LLM: 读懂 select_monthly_reports 提示词里的编号清单, 按 answer 回编号.

    真跑 classify_pdf_links 的解析/校验/分批代码 (只把模型换成可预期的桩),
    这样测的是我们自己的映射与校验逻辑, 而不是把它整段 mock 掉。

    answer(url) -> "YYYY-MM" 表示"这条是本基金月报"; 返回 None 表示不是。
    也可返回 ("YYYY-MM", date_text) 显式指定据以判月份的原文片段 —— 真模型必须
    交出这个片段, 代码会核对它逐字在链接里且真能解出该 ym
    (discover._ym_backed_by_link_text)。只给 ym 时, 桩用文件名当 date_text
    (文件名当然逐字在链接里, 真实归档页文件名也确实自带日期)。
    """

    def __init__(self, answer: Callable[[str], Any],
                 *, raise_times: int = 0, raw_text: Optional[str] = None) -> None:
        self._answer = answer
        self._raise_times = raise_times
        self._raw_text = raw_text
        self.prompts: List[str] = []

    def messages(self, prompt: str, max_tokens: Optional[int] = None) -> _Resp:
        self.prompts.append(prompt)
        if self._raise_times > 0:
            self._raise_times -= 1
            raise RuntimeError("upstream 503")
        if self._raw_text is not None:
            return _Resp(self._raw_text)
        reports, rejected = [], []
        for num, url in _LISTING_ITEM_RE.findall(prompt):
            ans = self._answer(url)
            if not ans:
                rejected.append({"i": int(num), "why": "不是本基金月报"})
                continue
            if isinstance(ans, tuple):
                ym, date_text = ans
            else:
                ym, date_text = ans, url.rsplit("/", 1)[-1].split("?", 1)[0]
            reports.append({"i": int(num), "ym": ym, "date_text": date_text})
        return _Resp(json.dumps({"reports": reports, "rejected": rejected}))
