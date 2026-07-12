"""阶段5：从旧 JSON/YAML 数据迁移到 SQLite。
只迁移原始数据（月度收益率、异常、RBA利率），指标由 webapp 后端计算。"""
import json
import sqlite3
import sys
from pathlib import Path

DB = Path("data/fund_analysis.db")
REGISTRY = Path("references/fund_registry.yaml")
RBA_JSON = Path("data/rba_cash_rate_history.json")
CLEANED = Path("data/cleaned")

def migrate_registry(conn):
    """从 fund_registry.yaml 导入基金元信息。"""
    import yaml
    if not REGISTRY.exists():
        print("SKIP fund_registry.yaml: not found")
        return 0
    with open(REGISTRY) as f:
        reg = yaml.safe_load(f)
    cur = conn.cursor()
    count = 0
    for fund_id, info in reg.items():
        try:
            cur.execute(
                "INSERT OR IGNORE INTO funds (fund_id, fund_name, apir_code, confirmed_url, fetch_method, url_type, verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    fund_id,
                    info.get("fund_name", fund_id),
                    info.get("apir_code"),
                    info.get("confirmed_url", ""),
                    info.get("fetch_method", "pdf"),
                    info.get("url_type", "factsheet"),
                    info.get("verified_at"),
                ),
            )
            if cur.rowcount:
                count += 1
                print(f"  INSERT fund: {fund_id}")
        except Exception as e:
            print(f"  ERROR fund {fund_id}: {e}")
    conn.commit()
    return count


def migrate_monthly_returns(conn):
    """从 data/cleaned/*/validated.json 的 time_series 导入月度收益率。"""
    cur = conn.cursor()
    total = 0
    # 每个基金只处理最新 validated 文件（跳过 history_cache，避免重复）
    for fund_dir in sorted(CLEANED.iterdir()):
        if not fund_dir.is_dir():
            continue
        vfiles = sorted(f for f in fund_dir.glob("*.validated.json") if "history_cache" not in f.name)
        if not vfiles:
            vfiles = sorted(fund_dir.glob("*.validated.json"))
        if not vfiles:
            continue
        vf = vfiles[-1]  # 取最新的
        with open(vf) as f:
            data = json.load(f)
        fund_id = data.get("fund_id", vf.parent.name)
        ts_list = data.get("time_series", [])
        if not ts_list:
            continue
        for row in ts_list:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO monthly_returns (fund_id, date, net_return, nav, commentary_truth) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        fund_id,
                        row["date"],
                        row["net_return"],
                        row.get("nav", 1.0),
                        row.get("commentary_truth"),
                    ),
                )
                if cur.rowcount:
                    total += 1
            except Exception as e:
                print(f"  ERROR {fund_id} {row.get('date')}: {e}")
        print(f"  {fund_id}: inserted {len(ts_list)} monthly returns")
    conn.commit()
    return total


def migrate_anomalies(conn):
    """从 validated.json 的 anomalies 导入异常数据。"""
    cur = conn.cursor()
    total = 0
    for fund_dir in sorted(CLEANED.iterdir()):
        if not fund_dir.is_dir():
            continue
        vfiles = sorted(f for f in fund_dir.glob("*.validated.json") if "history_cache" not in f.name)
        if not vfiles:
            vfiles = sorted(fund_dir.glob("*.validated.json"))
        if not vfiles:
            continue
        vf = vfiles[-1]
        with open(vf) as f:
            data = json.load(f)
        fund_id = data.get("fund_id", vf.parent.name)
        anom_list = data.get("anomalies", [])
        if not anom_list:
            continue
        for a in anom_list:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO anomalies (fund_id, date, value, z_score, threshold_sigma, mean, stdev) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        fund_id,
                        a["date"],
                        a["value"],
                        a.get("z_score") or a.get("mad_score", 0.0),
                        a.get("threshold_sigma", 3.0),
                        a.get("mean") or a.get("median", 0.0),
                        a.get("stdev") or a.get("mad", 0.0),
                    ),
                )
                if cur.rowcount:
                    total += 1
            except Exception as e:
                print(f"  ERROR anomaly {fund_id} {a.get('date')}: {e}")
        print(f"  {fund_id}: inserted {len(anom_list)} anomalies")
    conn.commit()
    return total


def migrate_rba_rates(conn):
    """从 rba_cash_rate_history.json 导入 RBA 利率。"""
    if not RBA_JSON.exists():
        print("SKIP rba_cash_rate_history.json: not found")
        return 0
    with open(RBA_JSON) as f:
        rates = json.load(f)
    cur = conn.cursor()
    count = 0
    for period, rate in rates.items():
        try:
            cur.execute(
                "INSERT OR IGNORE INTO rba_cash_rates (date_period, rate) VALUES (?, ?)",
                (period, rate),
            )
            if cur.rowcount:
                count += 1
        except Exception as e:
            print(f"  ERROR RBA {period}: {e}")
    conn.commit()
    print(f"  RBA rates: inserted {count} records")
    return count


def build_tables(conn):
    """建缺失的表。"""
    schema_sql = """
    CREATE TABLE IF NOT EXISTS anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_id TEXT NOT NULL REFERENCES funds(fund_id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        value REAL NOT NULL,
        z_score REAL NOT NULL,
        threshold_sigma REAL NOT NULL,
        mean REAL NOT NULL,
        stdev REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS rba_cash_rates (
        date_period TEXT PRIMARY KEY,
        rate REAL NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS fund_metrics (
        fund_id TEXT PRIMARY KEY REFERENCES funds(fund_id) ON DELETE CASCADE,
        date_period TEXT NOT NULL,
        history_months INTEGER NOT NULL,
        is_short_history_warning INTEGER NOT NULL,
        unsmoothing_coefficient_phi REAL NOT NULL,
        is_geltner_applied INTEGER NOT NULL,
        orig_annualized_excess_return REAL NOT NULL,
        un_annualized_excess_return REAL NOT NULL,
        orig_max_drawdown REAL NOT NULL,
        un_max_drawdown REAL NOT NULL,
        orig_omega_ratio REAL NOT NULL,
        un_omega_ratio REAL NOT NULL,
        orig_excess_win_rate REAL NOT NULL,
        un_excess_win_rate REAL NOT NULL,
        orig_max_underperform_months INTEGER NOT NULL,
        un_max_underperform_months INTEGER NOT NULL,
        orig_annualized_volatility REAL NOT NULL,
        un_annualized_volatility REAL NOT NULL,
        ljung_box_q REAL NOT NULL,
        is_q_significant INTEGER NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """
    cur = conn.cursor()
    for stmt in schema_sql.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s + ";")
    conn.commit()
    print("Tables ensured: anomalies, rba_cash_rates, fund_metrics")


def verify(conn):
    """打印迁移后统计。"""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM funds")
    funds = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM monthly_returns")
    returns = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM anomalies")
    anom = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM rba_cash_rates")
    rba = cur.fetchone()[0]
    print(f"\n=== 迁移后统计 ===")
    print(f"  funds:           {funds}")
    print(f"  monthly_returns: {returns}")
    print(f"  anomalies:       {anom}")
    print(f"  rba_cash_rates:  {rba}")
    print()
    cur.execute("SELECT fund_id, MIN(date), MAX(date), COUNT(*) FROM monthly_returns GROUP BY fund_id ORDER BY fund_id")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} ~ {row[2]} ({row[3]} months)")


def main():
    conn = sqlite3.connect(str(DB))
    build_tables(conn)

    print("=== 迁移基金注册表 ===")
    migrate_registry(conn)

    print("\n=== 迁移月度收益率 ===")
    migrate_monthly_returns(conn)

    print("\n=== 迁移异常数据 ===")
    migrate_anomalies(conn)

    print("\n=== 迁移 RBA 利率 ===")
    migrate_rba_rates(conn)

    verify(conn)
    conn.close()


if __name__ == "__main__":
    main()
