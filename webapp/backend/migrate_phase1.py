"""Phase 1 迁移脚本：fund_metrics / anomalies 派生缓存按新 schema 重建并重算。

变更（PDD Phase 1）：
- FundMetric: 删 omega，增 information_ratio/annualized_return/recovery_months/
  dd_recovered/excess_sample_months。
- Anomaly: 增 type/reason，数值字段改 nullable（支持 rba_missing）。

无 Alembic，create_all 不改旧表，故删除两张派生缓存表后按新 schema 重建。
源数据（funds/monthly_returns/rba_cash_rates/ai_reports）保留不动。

安全：执行前先备份 DB 文件；重算后校验持久化一致性，异常则提示从备份恢复。

用法:
    cd webapp/backend && python3 migrate_phase1.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app import models  # noqa: F401  注册所有模型
from app.crud import get_all_funds
from app.metrics_pipeline import compute_and_store_metrics
from app.models import Fund, FundMetric


def main() -> int:
    db_url = settings.DATABASE_URL

    # Step 0：备份 sqlite 文件库（修正1，零成本保险）
    db_path = None
    if db_url.startswith("sqlite:///"):
        candidate = db_url.replace("sqlite:///", "", 1)
        p = Path(candidate)
        if str(p) != ":memory:" and p.exists():
            db_path = p
    if db_path is not None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = db_path.with_name(f"{db_path.name}.bak-{ts}")
        shutil.copy(str(db_path), str(bak))
        print(f"[备份] {db_path} -> {bak}")
    else:
        print("[备份] 跳过（非 sqlite 文件库或库不存在）")

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()

    try:
        # Step 1：删除两张派生缓存表（仅派生缓存，源数据不动）
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS fund_metrics"))
            conn.execute(text("DROP TABLE IF EXISTS anomalies"))
        print("[迁移] 已删除 fund_metrics, anomalies（派生缓存，将重建）")

        # Step 2：按新 schema 重建
        Base.metadata.create_all(bind=engine)
        print("[迁移] 已按新 schema 重建表")

        # Step 3：遍历基金重算
        funds = get_all_funds(session)
        print(f"[重算] {len(funds)} 支基金")
        ok, fail = 0, 0
        for f in funds:
            try:
                compute_and_store_metrics(session, f.fund_id)
                ok += 1
            except ValueError as e:
                # 数据缺口/RBA 全缺等零容忍业务错误：跳过该基金（预期，非代码bug）
                fail += 1
                session.rollback()
                print(f"  [跳过] {f.fund_id}: {e}")
        session.commit()

        # Step 4：校验持久化一致性（修正1：不假设成功，验证它）
        metric_count = session.query(FundMetric).count()
        fund_count = session.query(Fund).count()
        print(f"[校验] fund_metrics={metric_count}, funds={fund_count}, "
              f"重算成功={ok}, 跳过={fail}")

        if ok == 0 and fund_count > 0:
            print("[错误] 所有基金重算失败（新代码可能有问题）。"
                  "请从备份 .bak 文件恢复后排查。", file=sys.stderr)
            return 1
        if metric_count != ok:
            print(f"[错误] fund_metrics 行数({metric_count}) != 重算成功数({ok})，"
                  f"持久化异常。请从备份 .bak 文件恢复。", file=sys.stderr)
            return 1
        if fail > 0:
            print(f"[提示] {fail} 支基金因数据缺口等被跳过（零容忍，预期行为，非代码bug）。")
        print("[完成] Phase 1 迁移成功")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
