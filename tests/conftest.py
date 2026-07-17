"""pytest conftest: 把 webapp/backend 加进 sys.path 让 test_ingest_priority_l1_l2
可 import `app.routers.ingest` 之类的 webapp 模块.

Spec D Phase E 修 pre-existing PYTHONPATH fail.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEBAPP_BACKEND = _REPO_ROOT / "webapp" / "backend"

for _p in (_REPO_ROOT, _WEBAPP_BACKEND):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)
