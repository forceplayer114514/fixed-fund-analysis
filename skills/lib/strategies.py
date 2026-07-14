"""skills 候选数据源策略探测层(集合差驱动 fallback 链)。

L0 local_cache -> L1 official_evergreen -> L2 wayback_cdx -> L3 fundmonitors,
每级输入 gap_set 只补洞,榨干(新增 0 或连 3 失败)才降级,gap 空早停。
**无 target 阈值**(36 是下游去平滑准入条件,爬取层禁知,修正 3.2.1)。

缺口非失败(修正 3.2.4):穷尽后 gap 非空入 confirmed_gaps,仅 obtained 空集
才 human_intervention。

下界单向向更早收敛(★重点1 / 补3):发现更早月 -> 下界前移 -> expected_range
扩展 -> gap_set 立即重算(新暴露早期月入队)。禁后缩(=静默删缺口)。

L2 CDX 单月快照上限(补1):每个缺失月份最多试时间戳最近 3 个快照,防 15 分钟
问题大规模复发。

纯探测层,不入库 -- 主会话拿 DiscoveryReport 后调 lib.ingest 入库(obtained ->
monthly_returns,gaps -> confirmed_gaps,pending_input -> LLM 补料重入)。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from lib import db as dbmod
from lib import extract as ex

# fallback 链级别(有序;删 distributions / third_party_rolling 占位)
LEVELS = [
    ("L0", "local_cache"),
    ("L1", "official_evergreen"),
    ("L2", "wayback_cdx"),
    ("L3", "fundmonitors"),
]
# 兼容:策略名有序列表(供外部断言遍历完整性)
STRATEGY_LIST = [name for _, name in LEVELS]

# L2 CDX 单月快照上限(补1):每个缺失月份最多试时间戳最近 3 个快照。
CDX_SNAPSHOTS_PER_MONTH = 3
# 连续失败降级阈值(本级新增 0 累计达此值仍继续降级,非强制停)
FAIL_STREAK_THRESHOLD = 3


@dataclass
class ProbeResult:
    """单级 probe 结果(集合差驱动)。

    new_months: 本级新增的 (year, month) 集合(去重,不含已 obtained)。
    pending_input: 缺输入(如 L1 归档页 URL / L3 fundmonitors FundID)时挂起,
        LLM 补料后重跑(断点续跑);非 None 表示本级因缺输入无法 probe。
    failures: 本级失败计数(probe 自报,run_discovery 另用 new 空判断 fail_streak)。
    evidence: 证据描述。
    unparseable: 本级解析失败链接日志(转 DiscoveryReport,不静默消失,★重点2)。
    ingest_payload: 入库载荷(confirmed_url/links/records/fetch_method),供 ingest
        按 best level 入库;本级无贡献时 None。
    """
    new_months: set = field(default_factory=set)
    pending_input: Optional[dict] = None
    failures: int = 0
    evidence: str = ""
    unparseable: list = field(default_factory=list)
    ingest_payload: Optional[dict] = None


@dataclass
class DiscoveryReport:
    fund_id: str
    inception_date: Optional[str] = None          # YYYY-MM-DD
    inception_assumed: bool = False
    obtained: list = field(default_factory=list)  # sorted ["YYYY-MM", ...]
    gaps: list = field(default_factory=list)      # sorted ["YYYY-MM", ...] 穷尽后正常产出
    per_level_contribution: dict = field(default_factory=dict)  # {"L0": n, ...}
    per_level_payload: dict = field(default_factory=dict)  # {"L0": ingest_payload, ...}
    unparseable_links: list = field(default_factory=list)
    pending_input: Optional[dict] = None          # 缺输入挂起(断点续跑)
    human_intervention_needed: bool = False       # 仅 obtained 空集 True
    evidence_log: list = field(default_factory=list)


ProbeFn = Callable[[dict, set], ProbeResult]


# ---------------------------------------------------------------------------
# 工具:月份集合运算
# ---------------------------------------------------------------------------

def _ym_to_str(y: int, m: int) -> str:
    return f"{y}-{m:02d}"


def _parse_ym(s: str) -> Optional[tuple]:
    """'YYYY-MM' 或 'YYYY-MM-DD' -> (year, month)。失败 None(不猜测)。"""
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12:
        return (y, mo)
    return None


def _month_range(lower: tuple, upper: tuple) -> set:
    """lower..upper(含)所有 (year, month)。"""
    months: set = set()
    y, m = lower
    while (y, m) <= upper:
        months.add((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _recent_published_month() -> tuple:
    """最近已发布报告月(距今 ≤2 自然月算正常滞后,不计缺口):当前月 -2。"""
    today = datetime.date.today()
    y, m = today.year, today.month
    for _ in range(2):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    return (y, m)


def _curl(url: str, timeout: int = 60) -> Optional[str]:
    """bash curl 兜底抓取(走系统网络绕 MCP 代理拦)。返回 stdout 或 None。"""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def _curl_download(url: str, dest: str, timeout: int = 60) -> bool:
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-o", dest, url],
            capture_output=True, timeout=timeout + 10,
        )
        return r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# probe 实现:L0-L3(集合差驱动,输入 gap_set 只补洞)
# ---------------------------------------------------------------------------

def probe_local_cache(fund_info: dict, gap_set: set) -> ProbeResult:
    """L0:查本地 DB 已有月份。返回 DB 全量已得月(供确立/收敛下界)。"""
    fund_id = fund_info.get("fund_id", "")
    db_path = fund_info.get("db_path")
    try:
        conn = dbmod.get_connection(db_path)
        rows = dbmod.get_monthly_returns(conn, fund_id)
        conn.close()
    except Exception as e:
        return ProbeResult(failures=1, evidence=f"DB 查询失败: {e}")
    if not rows:
        return ProbeResult(failures=1, evidence="DB 无该基金数据")
    months: set = set()
    for r in rows:
        ym = _parse_ym(r["date"])
        if ym:
            months.add(ym)
    return ProbeResult(
        new_months=months,
        evidence=f"DB 已有 {len(months)} 月数据",
        ingest_payload={"fetch_method": "local", "confirmed_url": "local:sqlite"},
    )


def probe_official_evergreen(fund_info: dict, gap_set: set) -> ProbeResult:
    """L1:官网归档页。fund_info 需含 confirmed_url(已验证归档页 URL,3.1.3)或
    archive_markdown(已抓取归档页 HTML/markdown)。

    抓归档页 -> extract_archive_links 提月份 PDF 链接 -> new_months(月份集合)
    + ingest_payload(links)。单 PDF Commentary/逐月表的下载与提取在 ingest(M4),
    本级只探测可得月份范围(分离探测与提取)。
    """
    archive = fund_info.get("archive_markdown")
    url = fund_info.get("confirmed_url")
    if not archive and not url:
        return ProbeResult(
            pending_input={"need": "confirmed_url",
                           "reason": "L1 需已验证归档页 URL(主会话定位后回填)"},
            evidence="fund_info 未提供 confirmed_url/archive_markdown",
        )
    if not archive:
        archive = _curl(url, timeout=60)
        if not archive:
            return ProbeResult(failures=1, evidence=f"curl 抓归档页失败: {url}")
    al = ex.extract_archive_links(archive)
    months: set = set()
    for ym_str, _pdf_url in al.parsed:
        ym = _parse_ym(ym_str)
        if ym:
            months.add(ym)
    if not months:
        return ProbeResult(
            failures=1,
            evidence="归档页无解析出月份的 PDF 链接",
            unparseable=al.unparseable,
        )
    return ProbeResult(
        new_months=months,
        evidence=f"归档页解析出 {len(months)} 月 PDF 链接",
        unparseable=al.unparseable,
        ingest_payload={
            "fetch_method": "pdf",
            "confirmed_url": url or "",
            "links": al.parsed,  # [(YYYY-MM, url), ...] 供 ingest 下载提取
        },
    )


def probe_wayback_cdx(fund_info: dict, gap_set: set) -> ProbeResult:
    """L2:Wayback CDX 历史快照,补 gap_set 中的洞。

    gap_set 非空:查 domain 下快照,从 original URL 提月份(extract_month_prefix),
    匹配 gap_set 的补;每缺失月至多 CDX_SNAPSHOTS_PER_MONTH 个快照(补1)。
    gap_set 空(范围未定):粗扫 domain 归档页模式快照计数(不知具体月份)。
    """
    domain = fund_info.get("issuer_domain")
    if not domain:
        return ProbeResult(failures=1, evidence="fund_info 未提供 issuer_domain")
    if gap_set:
        return _cdx_fill_gaps(domain, gap_set)
    return _cdx_scan_domain(domain)


def _cdx_fill_gaps(domain: str, gap_set: set) -> ProbeResult:
    """对 gap_set 每月,查 CDX 快照从 original URL 提月份补洞;每月至多 3 快照(补1)。"""
    patterns = [f"{domain}/*", f"{domain}/wp-content/uploads/*"]
    new_months: set = set()
    month_snap_count: dict = {}
    for pat in patterns:
        api = (
            f"http://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&limit=500"
        )
        out = _curl(api, timeout=30)
        if not out:
            continue
        try:
            arr = json.loads(out)
        except Exception:
            continue
        for row in arr[1:]:  # 首行表头跳过
            original = row[1] if len(row) > 1 else ""
            ym = ex.extract_month_prefix(original)
            if not ym:
                continue
            ym_t = _parse_ym(ym)
            if ym_t is None or ym_t not in gap_set:
                continue
            if month_snap_count.get(ym_t, 0) >= CDX_SNAPSHOTS_PER_MONTH:
                continue  # 补1:该月已达快照上限,不再计入
            month_snap_count[ym_t] = month_snap_count.get(ym_t, 0) + 1
            new_months.add(ym_t)
    if new_months:
        return ProbeResult(
            new_months=new_months,
            evidence=f"CDX 补洞 {len(new_months)} 月(每月至多{CDX_SNAPSHOTS_PER_MONTH}快照,补1)",
            ingest_payload={"fetch_method": "pdf", "confirmed_url": "web.archive.org"},
        )
    return ProbeResult(failures=1, evidence="CDX 补洞零命中(无 gap_set 月份的快照)")


def _cdx_scan_domain(domain: str) -> ProbeResult:
    """范围未定时扫 domain 归档页模式快照(粗扫,仅标记可得,不知具体月份)。"""
    patterns = [f"{domain}/performance*", f"{domain}/wp-content/uploads/*"]
    total = 0
    for pat in patterns:
        api = (
            f"http://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&limit=200"
        )
        out = _curl(api, timeout=30)
        if out:
            try:
                arr = json.loads(out)
                total += max(0, len(arr) - 1)
            except Exception:
                pass
    if total > 0:
        return ProbeResult(
            new_months=set(),  # 粗扫不知具体月份,仅标记可得
            evidence=f"CDX 命中 {total} 快照(范围未定,粗扫)",
            ingest_payload={"fetch_method": "pdf", "confirmed_url": "web.archive.org"},
        )
    return ProbeResult(failures=1, evidence="CDX 零快照")


def probe_fundmonitors(fund_info: dict, gap_set: set) -> ProbeResult:
    """L3:fundmonitors Full Profile AJAX 逐月表。

    fund_info 需含 fundmonitors_html_path(已抓取存文件)或 fundmonitors_url(代码
    curl 抓 AJAX)。缺输入 -> pending_input 挂起(LLM 补料重入)。付费墙 -> 跳过。
    """
    path = fund_info.get("fundmonitors_html_path")
    url = fund_info.get("fundmonitors_url")
    if not path and not url:
        return ProbeResult(
            pending_input={"need": "fundmonitors_url",
                           "reason": "L3 需 fundmonitors fund-profile URL(主会话定位后回填)"},
            evidence="fund_info 未提供 fundmonitors_html_path/url",
        )
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                md = f.read()
        except Exception as e:
            return ProbeResult(failures=1, evidence=f"读取失败: {e}")
    else:
        md = _curl(url, timeout=60)
        if not md:
            return ProbeResult(failures=1, evidence=f"curl 抓 fundmonitors 失败: {url}")
    low = md.lower()
    if "must be logged in" in low or "premium" in low:
        return ProbeResult(
            failures=1,
            evidence="fundmonitors 付费墙/登录墙,立即跳过不提供账号",
        )
    records, _ytd = ex.parse_html_monthly_table(md)
    if not records:
        return ProbeResult(failures=1, evidence="无 Historical Performance 逐月表")
    months: set = set()
    for date_str, _ret in records:
        ym = _parse_ym(date_str)
        if ym:
            months.add(ym)
    return ProbeResult(
        new_months=months,
        evidence=f"fundmonitors 逐月表 {len(months)} 月",
        ingest_payload={"fetch_method": "html", "confirmed_url": url or "",
                        "records": records},
    )


DEFAULT_PROBES: dict = {
    "local_cache": probe_local_cache,
    "official_evergreen": probe_official_evergreen,
    "wayback_cdx": probe_wayback_cdx,
    "fundmonitors": probe_fundmonitors,
}


# ---------------------------------------------------------------------------
# run_discovery:集合差驱动主循环(★重点1 下界收敛)
# ---------------------------------------------------------------------------

def run_discovery(
    fund_info: dict,
    expected_range: Optional[set] = None,
    probes: Optional[dict] = None,
) -> DiscoveryReport:
    """遍历 fallback 链 L0->L3,集合差驱动。

    expected_range: set[(year,month)] 期望月份范围。None 时从 fund_info 推:
      下界 = inception_date 月(无精确日 inception_assumed=True 时不信,待 probe
      确定最早月);上界 = latest_month 或当前月-2(≤2 自然月滞后不算缺口)。
    下界单向向更早收敛(★重点1):发现更早月 -> 下界前移 -> expected_range 扩展
      -> gap_set 重算(新暴露早期月入队)。禁后缩。
    降级:本级 new 空 或 连3失败 -> 下一级。早停:expected 非空且 gap 空。
    L3 缺输入 -> pending_input 挂起(断点续跑)。
    缺口非失败:穷尽后 gap 非空入 gaps;仅 obtained 空集 -> human_intervention。
    """
    probes = probes if probes is not None else DEFAULT_PROBES
    fund_id = fund_info.get("fund_id", "")
    inception_date = fund_info.get("inception_date")
    inception_assumed = bool(fund_info.get("inception_assumed", False))

    upper = _parse_ym(fund_info.get("latest_month")) or _recent_published_month()
    if expected_range is not None:
        expected = set(expected_range)
        lower = min(expected) if expected else None
    elif inception_date and not inception_assumed:
        lower = _parse_ym(inception_date)
        expected = _month_range(lower, upper) if lower else set()
    else:
        # 无 inception_date 或 inception_assumed(无精确日):下界待 probe 确定最早月
        lower = None
        expected = set()

    obtained: set = set()
    unparseable_all: list = []
    per_level: dict = {}
    per_level_payload: dict = {}
    evidence_log: list = []
    pending_input: Optional[dict] = None
    fail_streak = 0

    for level_key, name in LEVELS:
        gap_set = expected - obtained
        # 早停:expected 非空且 gap 空 -> skip 本级(continue 让后续级也 skip,
        # 保证剩余级别全部记 skip_reason=gap_empty;expected 空时仍需 probe 确定范围)
        if expected and not gap_set:
            evidence_log.append({"level": level_key, "strategy": name,
                                 "tried": False, "skip_reason": "gap_empty"})
            continue
        probe_fn = probes.get(name)
        if probe_fn is None:
            continue
        result = probe_fn(fund_info, gap_set)
        new = set(result.new_months or set())
        evidence_log.append({
            "level": level_key, "strategy": name, "tried": True,
            "new_count": len(new), "evidence": result.evidence,
            "pending_input": result.pending_input is not None,
        })
        if result.unparseable:
            unparseable_all.extend(result.unparseable)
        if result.pending_input:
            pending_input = result.pending_input
        obtained |= new
        per_level[level_key] = len(new)
        if result.ingest_payload:
            per_level_payload[level_key] = result.ingest_payload

        # ★重点1:下界单向向更早收敛(禁后缩)
        if new:
            new_min = min(new)
            if lower is None or new_min < lower:
                lower = new_min
                if lower is not None:
                    expected = _month_range(lower, upper)

        # 降级计数(本级无新增 -> fail_streak++)
        if new:
            fail_streak = 0
        else:
            fail_streak += 1

        # fail_streak >= FAIL_STREAK_THRESHOLD 仍继续下一级(降级,不强制停);
        # gap 空的早停在循环开头 skip_reason=gap_empty 处理(continue 让后续级全 skip)

    gaps = expected - obtained if expected else set()
    human = len(obtained) == 0
    return DiscoveryReport(
        fund_id=fund_id,
        inception_date=inception_date,
        inception_assumed=inception_assumed,
        obtained=sorted(_ym_to_str(y, m) for y, m in obtained),
        gaps=sorted(_ym_to_str(y, m) for y, m in gaps),
        per_level_contribution=per_level,
        per_level_payload=per_level_payload,
        unparseable_links=unparseable_all,
        pending_input=pending_input,
        human_intervention_needed=human,
        evidence_log=evidence_log,
    )


def _cli() -> int:
    parser = argparse.ArgumentParser(description="skills 候选数据源策略探测(集合差驱动)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="跑 fallback 链探测")
    p.add_argument("--fund-id", required=True)
    p.add_argument("--inception-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--inception-assumed", action="store_true")
    p.add_argument("--latest-month", default=None, help="YYYY-MM")
    p.add_argument("--confirmed-url", default=None, help="L1 已验证归档页 URL")
    p.add_argument("--issuer-domain", default=None)
    p.add_argument("--fundmonitors-url", default=None)
    p.add_argument("--db-path", default=None)
    args = parser.parse_args()

    fund_info = {
        "fund_id": args.fund_id,
        "inception_date": args.inception_date,
        "inception_assumed": args.inception_assumed,
        "latest_month": args.latest_month,
        "confirmed_url": args.confirmed_url,
        "issuer_domain": args.issuer_domain,
        "fundmonitors_url": args.fundmonitors_url,
        "db_path": args.db_path,
    }
    report = run_discovery(fund_info)
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
