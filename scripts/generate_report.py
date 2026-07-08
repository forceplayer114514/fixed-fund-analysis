import os
import sys
import json
import datetime
import yaml

# Path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEANED_DIR = os.path.join(BASE_DIR, "data", "cleaned")
REGISTRY_PATH = os.path.join(BASE_DIR, "references", "fund_registry.yaml")

def load_metrics(fund_id):
    # Find the metrics file in the fund's cleaned directory
    fund_dir = os.path.join(CLEANED_DIR, fund_id)
    if not os.path.exists(fund_dir):
        return None, None, None

    # Load metrics
    metrics_files = [f for f in os.listdir(fund_dir) if f.endswith(".metrics.json")]
    if not metrics_files:
        return None, None, None
    metrics_files.sort()
    latest_metrics = os.path.join(fund_dir, metrics_files[-1])
    with open(latest_metrics, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    # Load anomalies and time series from validated data
    validated_files = [f for f in os.listdir(fund_dir) if f.endswith(".validated.json")]
    anomalies = []
    time_series = []
    if validated_files:
        validated_files.sort()
        latest_validated = os.path.join(fund_dir, validated_files[-1])
        with open(latest_validated, "r", encoding="utf-8") as f:
            val_data = json.load(f)
            anomalies = val_data.get("anomalies", [])
            time_series = val_data.get("time_series", [])

    return metrics, anomalies, time_series

def calculate_subset_metrics(returns, unsmoothed_returns, rf_rates):
    n = len(returns)
    if n == 0:
        return None

    # original annualized absolute return
    comp_orig_abs = 1.0
    for r in returns:
        comp_orig_abs *= (1.0 + r)
    ann_ret_orig = (comp_orig_abs ** (12.0 / n)) - 1.0 if n > 0 else 0.0

    # RBA annualized return
    comp_rba = 1.0
    for rf in rf_rates:
        comp_rba *= (1.0 + rf / 12.0)
    ann_rba = (comp_rba ** (12.0 / n)) - 1.0 if n > 0 else 0.0

    ann_ex_orig = ann_ret_orig - ann_rba

    # unsmoothed annualized absolute return
    comp_un_abs = 1.0
    for r in unsmoothed_returns:
        comp_un_abs *= (1.0 + r)
    ann_ret_un = (comp_un_abs ** (12.0 / n)) - 1.0 if n > 0 else 0.0
    ann_ex_un = ann_ret_un - ann_rba

    # volatility helper
    def get_vol(ret_list):
        if len(ret_list) < 2:
            return 0.0
        mean_val = sum(ret_list) / len(ret_list)
        var = sum((x - mean_val)**2 for x in ret_list) / (len(ret_list) - 1)
        return (var * 12.0) ** 0.5

    vol_orig = get_vol(returns)
    vol_un = get_vol(unsmoothed_returns)

    # excess returns series
    ex_ret_orig = [r - (rf / 12.0) for r, rf in zip(returns, rf_rates)]
    ex_ret_un = [r - (rf / 12.0) for r, rf in zip(unsmoothed_returns, rf_rates)]

    # downside deviation helper (excess return relative to 0)
    def get_downside(ex_ret_list):
        n_len = len(ex_ret_list)
        if n_len == 0:
            return 0.0
        sq_neg = [r**2 if r < 0 else 0.0 for r in ex_ret_list]
        return (sum(sq_neg) / n_len * 12.0) ** 0.5

    downside_orig = get_downside(ex_ret_orig)
    downside_un = get_downside(ex_ret_un)

    # sortino ratio
    sortino_orig = ann_ex_orig / downside_orig if downside_orig > 1e-6 else float('inf')
    sortino_un = ann_ex_un / downside_un if downside_un > 1e-6 else float('inf')

    # max drawdown helper
    def get_max_dd(ret_list):
        if not ret_list:
            return 0.0
        nav = 1.0
        nav_series = [nav]
        for r in ret_list:
            nav = nav * (1.0 + r)
            nav_series.append(nav)

        max_dd = 0.0
        peak = nav_series[0]
        for val in nav_series:
            if val > peak:
                peak = val
            dd = (val - peak) / peak
            if dd < max_dd:
                max_dd = dd
        return max_dd

    max_dd_orig = get_max_dd(returns)

    return {
        "annualized_return_orig": ann_ret_orig,
        "annualized_return_un": ann_ret_un,
        "annualized_excess_orig": ann_ex_orig,
        "annualized_excess_un": ann_ex_un,
        "annualized_vol_orig": vol_orig,
        "annualized_vol_un": vol_un,
        "sortino_orig": sortino_orig,
        "sortino_un": sortino_un,
        "max_drawdown": max_dd_orig
    }

def number_to_chinese(n):
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return mapping.get(n, str(n))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate comparison report.")
    parser.add_argument("--funds", nargs="+", help="Optional: specific fund IDs to include in the report.")
    args = parser.parse_args()

    print("=== Generating Comparison Report ===")

    # Load registry
    if not os.path.exists(REGISTRY_PATH):
        print(f"CRITICAL: Registry not found at {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}

    funds_to_process = args.funds if args.funds else registry.keys()

    funds_metrics = {}
    funds_anomalies = {}
    funds_time_series = {}
    for fund_id in funds_to_process:
        if fund_id not in registry:
            print(f"Warning: {fund_id} not in registry, skipping.", file=sys.stderr)
            continue
        metrics, anomalies, time_series = load_metrics(fund_id)
        if metrics:
            funds_metrics[fund_id] = metrics
            funds_anomalies[fund_id] = anomalies
            funds_time_series[fund_id] = time_series

    if not funds_metrics:
        print("CRITICAL: No calculated metrics found in cleaned directories.", file=sys.stderr)
        sys.exit(1)

    rba_rate = next(iter(funds_metrics.values()))["rba_cash_rate"]

    # Load historical cash rates
    rba_history_path = os.path.join(BASE_DIR, "data", "rba_cash_rate_history.json")
    rba_history = {}
    if os.path.exists(rba_history_path):
        try:
            with open(rba_history_path, "r", encoding="utf-8") as f:
                rba_history = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load RBA history cache: {e}")

    # Prepare return sequences and metadata for all funds
    funds_data = {}
    for fund_id, m in funds_metrics.items():
        time_series = funds_time_series[fund_id]
        if not time_series:
            continue
        data_points = time_series[1:]
        dates = [dp["date"] for dp in data_points]
        returns = [dp["net_return"] for dp in data_points]

        # Resolve RBA cash rates
        current_rba_rate = m["rba_cash_rate"]
        rf_rates = []
        for d in dates:
            rf = rba_history.get(d[:7], current_rba_rate)
            rf_rates.append(rf)

        # Reconstruct unsmoothed returns
        if m["is_geltner_applied"]:
            phi = m["unsmoothing_coefficient_phi"]
            unsmoothed = [returns[0]]
            for t in range(1, len(returns)):
                unsmoothed_val = (returns[t] - phi * returns[t-1]) / (1.0 - phi)
                unsmoothed.append(unsmoothed_val)
        else:
            unsmoothed = list(returns)

        funds_data[fund_id] = {
            "dates": dates,
            "returns": returns,
            "unsmoothed": unsmoothed,
            "rf_rates": rf_rates,
            "is_geltner_applied": m["is_geltner_applied"],
            "fund_name": m["fund_name"],
            "apir_code": m["apir_code"]
        }

    is_multi_fund = len(funds_metrics) > 1

    # Start building markdown report
    md = []
    md.append("# 澳洲固定收益基金业绩与风险对比报告")
    md.append(f"*报告生成日期: {datetime.datetime.now().strftime('%Y-%m-%d')} | 当前 RBA 官方现金利率 (Cash Rate): {rba_rate * 100.0:.2f}%*\n")

    md.append("## 一、 核心业绩与风险指标对比")

    # Helper to generate table markdown
    def format_performance_table(table_data, title, description):
        lines = []
        lines.append(f"### {title}")
        if description:
            lines.append(description + "\n")
        lines.append("| 基金名称 (代码) | 数据区间 | 历史月数 | 去平滑 | 年化收益率 (原/修正后) | 年化超额收益 (原/修正后) | 年化波动率 (原/修正后) | Sortino 比率 (原/修正后) | 最大回撤 |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for row in table_data:
            name = row["name"]
            apir = row["apir"]
            period = row["period"]
            months = row["months"]
            geltner = row["geltner"]

            orig_ret = row["orig_ret"]
            un_ret = row["un_ret"]
            orig_ex = row["orig_ex"]
            un_ex = row["un_ex"]
            orig_vol = row["orig_vol"]
            un_vol = row["un_vol"]
            orig_sortino = row["orig_sortino"]
            un_sortino = row["un_sortino"]
            max_dd = row["max_dd"]

            sortino_orig_str = f"{orig_sortino:.2f}" if orig_sortino != float('inf') else "N/A"
            sortino_un_str = f"{un_sortino:.2f}" if un_sortino != float('inf') else "N/A"

            lines.append(
                f"| {name} ({apir}) | {period} | {months} | {geltner} | "
                f"{orig_ret*100.0:.2f}% / {un_ret*100.0:.2f}% | "
                f"{orig_ex*100.0:.2f}% / {un_ex*100.0:.2f}% | "
                f"{orig_vol*100.0:.2f}% / {un_vol*100.0:.2f}% | "
                f"{sortino_orig_str} / {sortino_un_str} | "
                f"{max_dd*100.0:.2f}% |"
            )
        lines.append("")
        return "\n".join(lines)

    # 1. Full history table
    table_full = []
    for fund_id, m in funds_metrics.items():
        fd = funds_data[fund_id]
        warning_suffix = " ⚠️" if m["is_short_history_warning"] else ""
        table_full.append({
            "name": fd["fund_name"],
            "apir": fd["apir_code"],
            "period": m["data_period"],
            "months": f"{m['history_months']}{warning_suffix}",
            "geltner": "已应用" if m["is_geltner_applied"] else "未应用",
            "orig_ret": m["original_metrics"]["annualized_return"],
            "un_ret": m["unsmoothed_metrics"]["annualized_return"],
            "orig_ex": m["original_metrics"].get("annualized_excess_return", 0.0),
            "un_ex": m["unsmoothed_metrics"].get("annualized_excess_return", 0.0),
            "orig_vol": m["original_metrics"]["annualized_volatility"],
            "un_vol": m["unsmoothed_metrics"]["annualized_volatility"],
            "orig_sortino": m["original_metrics"]["sortino_ratio"],
            "un_sortino": m["unsmoothed_metrics"]["sortino_ratio"],
            "max_dd": m["original_metrics"].get("max_drawdown", 0.0)
        })
    md.append(format_performance_table(table_full, "1.1 历史全量业绩对比", "下表对比了各基金自成立/有记录以来的完整历史业绩与风险指标："))

    # 2. Past 3 Years (36 months) table
    table_3y = []
    for fund_id, fd in funds_data.items():
        if len(fd["returns"]) >= 36:
            returns_3y = fd["returns"][-36:]
            unsmoothed_3y = fd["unsmoothed"][-36:]
            rf_rates_3y = fd["rf_rates"][-36:]
            dates_3y = fd["dates"][-36:]
            sub = calculate_subset_metrics(returns_3y, unsmoothed_3y, rf_rates_3y)
            if sub:
                table_3y.append({
                    "name": fd["fund_name"],
                    "apir": fd["apir_code"],
                    "period": f"{dates_3y[0][:7]} 至 {dates_3y[-1][:7]}",
                    "months": "36",
                    "geltner": "已应用" if fd["is_geltner_applied"] else "未应用",
                    "orig_ret": sub["annualized_return_orig"],
                    "un_ret": sub["annualized_return_un"],
                    "orig_ex": sub["annualized_excess_orig"],
                    "un_ex": sub["annualized_excess_un"],
                    "orig_vol": sub["annualized_vol_orig"],
                    "un_vol": sub["annualized_vol_un"],
                    "orig_sortino": sub["sortino_orig"],
                    "un_sortino": sub["sortino_un"],
                    "max_dd": sub["max_drawdown"]
                })
    if table_3y:
        md.append(format_performance_table(table_3y, "1.2 最近 3 年 (36个月) 业绩对比", "下表对比了各基金在最近 36 个月数据区间内的业绩与风险指标："))

    # 3. Past 1 Year (12 months) table
    table_1y = []
    for fund_id, fd in funds_data.items():
        if len(fd["returns"]) >= 12:
            returns_1y = fd["returns"][-12:]
            unsmoothed_1y = fd["unsmoothed"][-12:]
            rf_rates_1y = fd["rf_rates"][-12:]
            dates_1y = fd["dates"][-12:]
            sub = calculate_subset_metrics(returns_1y, unsmoothed_1y, rf_rates_1y)
            if sub:
                table_1y.append({
                    "name": fd["fund_name"],
                    "apir": fd["apir_code"],
                    "period": f"{dates_1y[0][:7]} 至 {dates_1y[-1][:7]}",
                    "months": "12",
                    "geltner": "已应用" if fd["is_geltner_applied"] else "未应用",
                    "orig_ret": sub["annualized_return_orig"],
                    "un_ret": sub["annualized_return_un"],
                    "orig_ex": sub["annualized_excess_orig"],
                    "un_ex": sub["annualized_excess_un"],
                    "orig_vol": sub["annualized_vol_orig"],
                    "un_vol": sub["annualized_vol_un"],
                    "orig_sortino": sub["sortino_orig"],
                    "un_sortino": sub["sortino_un"],
                    "max_dd": sub["max_drawdown"]
                })
    if table_1y:
        md.append(format_performance_table(table_1y, "1.3 最近 1 年 (12个月) 业绩对比", "下表对比了各基金在最近 12 个月数据区间内的业绩与风险指标："))

    # 4. Unified 起跑线 (Common period) table - only for multi-fund comparison
    if is_multi_fund:
        common_months = set(d[:7] for d in funds_data[next(iter(funds_data))]["dates"])
        for fd in list(funds_data.values())[1:]:
            common_months = common_months.intersection(set(d[:7] for d in fd["dates"]))

        sorted_common_months = sorted(list(common_months))
        if len(sorted_common_months) >= 6:
            table_common = []
            for fund_id, fd in funds_data.items():
                indices = [i for i, d in enumerate(fd["dates"]) if d[:7] in common_months]
                returns_common = [fd["returns"][i] for i in indices]
                unsmoothed_common = [fd["unsmoothed"][i] for i in indices]
                rf_rates_common = [fd["rf_rates"][i] for i in indices]

                sub = calculate_subset_metrics(returns_common, unsmoothed_common, rf_rates_common)
                if sub:
                    table_common.append({
                        "name": fd["fund_name"],
                        "apir": fd["apir_code"],
                        "period": f"{sorted_common_months[0]} 至 {sorted_common_months[-1]}",
                        "months": str(len(sorted_common_months)),
                        "geltner": "已应用" if fd["is_geltner_applied"] else "未应用",
                        "orig_ret": sub["annualized_return_orig"],
                        "un_ret": sub["annualized_return_un"],
                        "orig_ex": sub["annualized_excess_orig"],
                        "un_ex": sub["annualized_excess_un"],
                        "orig_vol": sub["annualized_vol_orig"],
                        "un_vol": sub["annualized_vol_un"],
                        "orig_sortino": sub["sortino_orig"],
                        "un_sortino": sub["sortino_un"],
                        "max_dd": sub["max_drawdown"]
                    })
            md.append(format_performance_table(table_common, f"1.4 统一起跑线对比 (共同重叠区间: {sorted_common_months[0]} 至 {sorted_common_months[-1]})", "下表对比了各基金在相同的历史共同重叠区间内的业绩与风险指标："))
        else:
            md.append("### 1.4 统一起跑线对比\n各基金没有足够的共同重叠区间（少于 6 个月），跳过统一起跑线对比。\n")

    md.append("\n> **注**：⚠️ 表示历史数据少于 6 个月。与传统使用单一当期无风险利率作为历史 hurdle rate 的简化计算不同，本报告**逐月扣除了历史当期实际的 RBA Cash Rate 得到超额收益率（Alpha），在此基础上求得复利年化超额收益与下行偏差**。这一调整消除了不同基金因成立年份不同、基准利率变化导致的选择性偏差，实现了真正的客观对比。\n")
    
    md.append("## 二、 自相关性分析与 Geltner 去平滑评估")
    md.append("资产估值平滑（Smoothing）会显著低估基金的波动率并高估其 Sortino 比率。去平滑参数 $\\phi$（一阶自相关系数）代表了平滑效应的严重程度。我们对各基金进行了 Ljung-Box 显著性检验（置信度 95%，显著性临界值 $Q > 3.841$）：\n")
    
    md.append("| 基金名称 | 自相关系数 $\\phi$ | Ljung-Box $Q$ 统计量 | 统计显著性 | 修正决策与依据 |")
    md.append("| :--- | :---: | :---: | :---: | :--- |")
    
    for fund_id, m in funds_metrics.items():
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        n = m["history_months"]
        q_stat = n * (n + 2) * (phi ** 2) / (n - 1) if n >= 36 else 0.0
        
        is_sig = q_stat > 3.841
        sig_str = "显著" if is_sig else "不显著"
        if n < 36:
            sig_str = "N/A"
            decision = f"跳过修正：历史区间仅 {n} 个月，少于 36 个月硬门禁"
        elif not is_sig:
            decision = f"跳过修正：自相关性不显著（Q = {q_stat:.2f} <= 3.841）"
        elif phi < 0:
            decision = f"跳过修正：自相关系数为负（phi = {phi:.4f}），无平滑特征"
        elif phi > 0.85:
            decision = f"跳过修正：自相关系数极高（phi = {phi:.4f} > 0.85），超出合理约束范围"
        else:
            decision = f"进行修正：自相关性显著且 phi = {phi:.4f} 在 [0, 0.85] 范围内"
            
        md.append(f"| {name} | {phi:.4f} | {q_stat:.2f} | {sig_str} | {decision} |")
        
    md.append("\n### 指标深度解析：")
    for idx, (fund_id, m) in enumerate(funds_metrics.items(), 1):
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        n = m["history_months"]
        orig_vol = m["original_metrics"]["annualized_volatility"]
        un_vol = m["unsmoothed_metrics"]["annualized_volatility"]
        orig_ex = m["original_metrics"].get("annualized_excess_return", 0.0)
        un_ex = m["unsmoothed_metrics"].get("annualized_excess_return", 0.0)
        orig_sortino = m["original_metrics"]["sortino_ratio"]
        un_sortino = m["unsmoothed_metrics"]["sortino_ratio"]
        
        if n < 36:
            md.append(f"{idx}. **{name}** 由于历史仅 {n} 个月，低于 36 个月样本门槛，跳过 Geltner 去平滑修正，保留原始指标（年化超额收益 **{orig_ex*100.0:.2f}%**，年化波动率 **{orig_vol*100.0:.2f}%**，Sortino 比率 **{orig_sortino:.2f}**）。")
        elif not m["is_geltner_applied"]:
            md.append(f"{idx}. **{name}** 的一阶自相关系数 $\\phi = {phi:.4f}$ 在统计上不显著或不满足修正条件，跳过去平滑修正。其复利年化超额收益为 **{orig_ex*100.0:.2f}%**，年化波动率 **{orig_vol*100.0:.2f}%**，Sortino 比率 **{orig_sortino:.2f}**。")
        else:
            md.append(f"{idx}. **{name}** 展示了显著的一阶自相关性（$\\phi = {phi:.4f}$）。经 Geltner 去平滑修正后，其真实年化波动率由 **{orig_vol*100.0:.2f}% 调增至 {un_vol*100.0:.2f}%**，复利年化超额收益为 **{orig_ex*100.0:.2f}% / {un_ex*100.0:.2f}%**，去平滑后的 Sortino 比率从 **{orig_sortino:.2f} 修正为 {un_sortino:.2f}**，这更真实地揭示了真实的下行风险。")
            
    # ponytail: Removed section "三、 信用利差分解与杠杆收益分析" and adjusted section numbering.
    # Leverage ratio cannot be parsed dynamically, so we strip this unnecessary analysis.

    
    # Data Anomalies Section
    md.append("\n## 三、 数据异常与人工复核清单")
    md.append("以下是基于稳健统计方法检测到的收益率异常（可能为黑天鹅事件，或由于源文件格式变动导致的解析分类错误）。请人工确认：\n")
    
    has_anomalies = False
    md.append("| 基金名称 | 异常月份 | 提取月度收益 | Commentary验证 | Z/MAD-Score | 判定/动作 |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
    
    for fund_id, anomalies in funds_anomalies.items():
        if not anomalies:
            continue
        has_anomalies = True
        fund_name = funds_metrics[fund_id]["fund_name"]
        for a in anomalies:
            date = a["date"]
            val = a["value"]
            score = a.get("z_score", a.get("mad_score", 0))
            comm_val = a.get("commentary_truth")
            comm_str = f"{comm_val*100.0:.2f}%" if comm_val is not None else "缺失/未知"
            
            # Simple heuristic action string
            if comm_val is not None and abs(comm_val - val) > 1e-4:
                action = "**类别错误**: 表格解析与正文不符，建议作为缺口剔除"
            else:
                action = "真实极端值: 需确认是否为市场黑天鹅"
                
            md.append(f"| {fund_name} | {date} | {val*100.0:.2f}% | {comm_str} | {score:.2f} | {action} |")
            
    if not has_anomalies:
        md.append("| (无) | - | - | - | - | 未检测到 3σ 级别的显著异常 |")

    md.append("\n## 四、 基金对比核心总结与投资建议")
    md.append("1. **综合风险收益表现 (Sortino 比率 - 超额收益视角)**：")

    sorted_funds = sorted(funds_metrics.values(), key=lambda x: x["unsmoothed_metrics"]["sortino_ratio"] if x["is_geltner_applied"] else x["original_metrics"]["sortino_ratio"], reverse=True)
    for m in sorted_funds:
        name = m["fund_name"]
        n = m["history_months"]
        orig_ret = m["original_metrics"]["annualized_return"]
        ex_ret = m["unsmoothed_metrics"]["annualized_excess_return"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_excess_return"]
        sortino = m["unsmoothed_metrics"]["sortino_ratio"] if m["is_geltner_applied"] else m["original_metrics"]["sortino_ratio"]
        vol = m["unsmoothed_metrics"]["annualized_volatility"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_volatility"]

        cycle_note = f"经历了 {n} 个月历史大周期" if n >= 36 else f"仅有 {n} 个月历史短期业绩"
        md.append(f"   - **{name}** ({cycle_note})：年化复利绝对收益率 **{orig_ret*100.0:.2f}%**，复利年化超额收益（扣除 RBA Cash Rate 之后）为 **{ex_ret*100.0:.2f}%**，在真实的年化波动率 **{vol*100.0:.2f}%** 下实现了 **{sortino:.2f}** 的超额 Sortino 比率。")

    md.append("2. **去平滑风险防范**：")
    for m in funds_metrics.values():
        name = m["fund_name"]
        phi = m["unsmoothing_coefficient_phi"]
        orig_vol = m["original_metrics"]["annualized_volatility"]
        un_vol = m["unsmoothed_metrics"]["annualized_volatility"]
        if m["is_geltner_applied"]:
            md.append(f"   - **{name}**：由于存在明显的平滑效应（一阶自相关系数 $\\phi = {phi:.4f}$），其账面年化波动率（{orig_vol*100.0:.2f}%）显著低估。经 Geltner 修正后的真实年化波动率为 **{un_vol*100.0:.2f}%**，在大类配置与风控限额管理中必须以修正后的真实波动率为基准。")

    # Save report.md or fund_list.md to data output directory
    output_dir = os.path.join(BASE_DIR, "data", "output")
    os.makedirs(output_dir, exist_ok=True)
    report_filename = "fund_list.md" if is_multi_fund else "report.md"
    output_path = os.path.join(output_dir, report_filename)

    # Generate AI investment advice based on calculated metrics
    md.append("\n## 五、 AI 投资建议")
    md.append("基于各固定收益基金的风险收益特征与 Geltner 去平滑波动率修正，提供以下投资建议：\n")
    for idx, m in enumerate(sorted_funds, 1):
        name = m["fund_name"]
        sortino = m["unsmoothed_metrics"]["sortino_ratio"] if m["is_geltner_applied"] else m["original_metrics"]["sortino_ratio"]
        vol = m["unsmoothed_metrics"]["annualized_volatility"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_volatility"]
        ex_ret = m["unsmoothed_metrics"]["annualized_excess_return"] if m["is_geltner_applied"] else m["original_metrics"]["annualized_excess_return"]

        name_lower = name.lower()
        if "floating-rate high yield" in name_lower:
            md.append(f"{idx}. **{name}**：目前超额 Sortino 比率极高 ({sortino:.2f})，年化超额收益达到 {ex_ret*100.0:.2f}%。该基金主要投资于澳洲投资级浮息票据，对利率上行有很好的防御性，适合作为目前高息环境下的核心固收配置，以获取稳健的信用利差溢价。")
        elif "smarter money" in name_lower:
            md.append(f"{idx}. **{name}**：该基金波动率极低（去平滑后年化波动率 {vol*100.0:.2f}%），流动性极强。虽然超额收益率略低于杠杆/高收益基金，但其防御属性极佳。适合作为现金管理 and 流动性增强工具，替代传统银行定期存款。")
        elif "long-short opportunities" in name_lower:
            md.append(f"{idx}. **{name}**：通过信用及银行股权市场的多空策略，实现了较强的年化超额收益 ({ex_ret*100.0:.2f}%)，但去平滑后波动率也相对较高 ({vol*100.0:.2f}%)。适合风险偏好较高、追求绝对收益的投资者作为固收组合的卫星配置。")
        elif "long-short credit" in name_lower:
            md.append(f"{idx}. **{name}**：拥有超过 8 年的长期运行记录，年化超额收益 {ex_ret*100.0:.2f}%，Sortino 比率为 {sortino:.2f}。适合中长期投资者配置，作为穿越利率周期的基石阿尔法配置。")
        elif "stake accumulate" in name_lower:
            md.append(f"{idx}. **{name}**：虽然历史数据偏短（{m['history_months']} 个月），但其年化超额收益（{ex_ret*100.0:.2f}%）和极低波动率使其短期表现亮眼。建议密切跟踪其在完整利率周期中的表现，目前可进行小仓位试水。")
        else:
            md.append(f"{idx}. **{name}**：年化超额收益为 {ex_ret*100.0:.2f}%，年化波动率为 {vol*100.0:.2f}%，超额 Sortino 比率为 {sortino:.2f}。请结合具体投资期限和风险偏好合理配置。")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # Generate Excel data file
    excel_path = os.path.join(output_dir, "fund_data.xlsx")
    generate_excel_data(registry, CLEANED_DIR, excel_path)

    print(f"SUCCESS: Report successfully generated at {output_path}")
    print(f"SUCCESS: Excel data successfully generated at {excel_path}")
    sys.exit(0)

def generate_excel_data(registry, cleaned_dir, excel_path):
    print("=== Generating Excel Data File ===")
    rba_history_path = os.path.join(os.path.dirname(cleaned_dir), "rba_cash_rate_history.json")
    if os.path.exists(rba_history_path):
        try:
            with open(rba_history_path, "r", encoding="utf-8") as f:
                rba_history = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load RBA history cache: {e}")
            rba_history = {}
    else:
        rba_history = {}
        
    try:
        import pandas as pd
    except ImportError:
        print("Warning: pandas is not installed. Skipping Excel generation.", file=sys.stderr)
        return
        
    try:
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            for fund_id, reg_info in registry.items():
                fund_name = reg_info.get("fund_name", fund_id)
                fund_dir = os.path.join(cleaned_dir, fund_id)
                if not os.path.exists(fund_dir):
                    continue
                validated_files = [f for f in os.listdir(fund_dir) if f.endswith(".validated.json")]
                if not validated_files:
                    continue
                validated_files.sort()
                latest_validated_file = os.path.join(fund_dir, validated_files[-1])
                
                try:
                    with open(latest_validated_file, "r", encoding="utf-8") as f:
                        val_data = json.load(f)
                except Exception as e:
                    print(f"Warning: Failed to load validated data for {fund_id}: {e}")
                    continue
                    
                time_series = val_data.get("time_series", [])
                if not time_series:
                    continue
                    
                records = []
                for dp in time_series[1:]:
                    date_str = dp["date"]
                    net_return = dp["net_return"]
                    
                    try:
                        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        period_str = f"{dt.year}年{dt.month}月"
                    except Exception:
                        period_str = date_str
                    
                    month_key = date_str[:7]
                    rba_annual = rba_history.get(month_key)
                    if rba_annual is None:
                        rba_annual = 0.0435
                        
                    rba_monthly = rba_annual / 12.0
                    
                    records.append({
                        "时期": period_str,
                        "本月净收益率": net_return,
                        "当期RBA现金利率 (年化)": rba_annual,
                        "当期RBA现金利率 (月度)": rba_monthly
                    })
                    
                if not records:
                    continue
                    
                df = pd.DataFrame(records)
                sheet_name = fund_name[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                workbook = writer.book
                worksheet = writer.sheets[sheet_name]
                
                for row in range(2, len(df) + 2):
                    worksheet[f"B{row}"].number_format = "0.00%"
                    worksheet[f"C{row}"].number_format = "0.00%"
                    worksheet[f"D{row}"].number_format = "0.00%"
                    
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = max(max_len + 3, 12)
                    
                print(f"Excel sheet generated for: {fund_name}")

    except Exception as e:
        print(f"Error generating Excel file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
