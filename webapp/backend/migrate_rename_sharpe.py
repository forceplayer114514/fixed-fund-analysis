"""迁移：fund_metrics 列 information_ratio -> sharpe_ratio（纯正名，值不变）。

背景：维度3「性价比」指标以 e = r_fund - RBA/12 为超额序列，分子 mean(e)、
分母 std(e, ddof=1)、年化 *sqrt(12)。基准是 RBA 现金利率（无风险利率），
现金基准下 tracking error 退化为总波动率——该统计量是 ex-post 夏普比率
（Sharpe 1994 修订定义），不是信息比率。2026-07 正名，算法一字节未动，
见 app/calculations.py::calculate_sharpe_ratio。

因是纯列重命名（数值恒等），用 SQLite `ALTER TABLE ... RENAME COLUMN`
就地改列名，不 drop 表、不重算、不触碰源数据。SQLite >= 3.25 支持。
幂等：若目标列已存在（已迁移）则跳过。

用法:
    cd webapp/backend && python3 migrate_rename_sharpe.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.config import settings

RENAMES = [
    ("orig_information_ratio", "orig_sharpe_ratio"),
    ("un_information_ratio", "un_sharpe_ratio"),
]


def main() -> int:
    db_url = settings.DATABASE_URL

    # Step 0：备份 sqlite 文件库（零成本保险）
    if db_url.startswith("sqlite:///"):
        candidate = db_url.replace("sqlite:///", "", 1)
        p = Path(candidate)
        if str(p) != ":memory:" and p.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = p.with_name(f"{p.name}.bak-sharpe-{ts}")
            shutil.copy(str(p), str(bak))
            print(f"[备份] {p} -> {bak}")

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args)

    insp = inspect(engine)
    if "fund_metrics" not in insp.get_table_names():
        print("[跳过] fund_metrics 表不存在（尚未初始化）")
        return 0
    cols = {c["name"] for c in insp.get_columns("fund_metrics")}

    with engine.begin() as conn:
        for old, new in RENAMES:
            if new in cols:
                print(f"[跳过] {new} 已存在（已迁移）")
                continue
            if old not in cols:
                print(f"[错误] 源列 {old} 不存在，且目标列 {new} 也不存在——"
                      f"schema 异常，人工核查。", file=sys.stderr)
                return 1
            conn.execute(text(
                f"ALTER TABLE fund_metrics RENAME COLUMN {old} TO {new}"
            ))
            print(f"[迁移] fund_metrics.{old} -> {new}")

    # 校验
    cols_after = {c["name"] for c in inspect(engine).get_columns("fund_metrics")}
    missing = [new for _, new in RENAMES if new not in cols_after]
    if missing:
        print(f"[错误] 迁移后目标列仍缺失: {missing}。请从 .bak 恢复。", file=sys.stderr)
        return 1
    print("[完成] sharpe 正名迁移成功（数值未动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
